"""F46/F136: /api/scanner/collate.

F46: the merged-PDF work directory is a fresh `tempfile.mkdtemp()` per call
(cleaned up in `finally`), not a name derived from the shared source scan
file -- two concurrent collates sharing a source image used to be able to
unlink the file the other was still reading.

F136: the requested `output_filename` is honored for the stored file
instead of being silently dropped in favor of a bare `{uuid}.pdf`.
"""
import asyncio
import os

from PIL import Image
from sqlalchemy import select

from app.models import AppConfig, ScanJob
from app.routers.scanner import _collate_pdfs_sync
from app.services import settings_cache


async def _seed_setting(db, key: str, value: str) -> None:
    db.add(AppConfig(key=key, value=value))
    await db.commit()
    settings_cache.invalidate_all()


async def _seed_two_image_scans(db, tmp_path):
    img1 = tmp_path / "a.png"
    Image.new("L", (100, 100), 255).save(img1)
    img2 = tmp_path / "b.png"
    Image.new("L", (100, 100), 255).save(img2)

    job1 = ScanJob(
        scan_id="scan-a", status="completed", filepath=str(img1),
        format="png", resolution=300, page_count=1,
    )
    job2 = ScanJob(
        scan_id="scan-b", status="completed", filepath=str(img2),
        format="png", resolution=300, page_count=1,
    )
    db.add_all([job1, job2])
    await db.commit()
    return img1, img2


async def test_collate_honors_output_filename(db, user_client, tmp_path):
    await _seed_setting(db, "scan_dir", str(tmp_path))
    await _seed_two_image_scans(db, tmp_path)

    resp = await user_client.post(
        "/api/scanner/collate",
        json={"scan_ids": ["scan-a", "scan-b"], "output_filename": "Invoice 2026.pdf"},
    )
    assert resp.status_code == 201
    scan_id = resp.json()["scan_id"]

    result = await db.execute(select(ScanJob).where(ScanJob.scan_id == scan_id))
    merged = result.scalar_one()

    basename = os.path.basename(merged.filepath)
    assert basename.endswith("_Invoice_2026.pdf")
    assert os.path.exists(merged.filepath)


async def test_collate_default_output_filename(db, user_client, tmp_path):
    """No output_filename supplied -- CollateRequest's own default
    ("merged.pdf") is used rather than a bare uuid."""
    await _seed_setting(db, "scan_dir", str(tmp_path))
    await _seed_two_image_scans(db, tmp_path)

    resp = await user_client.post(
        "/api/scanner/collate", json={"scan_ids": ["scan-a", "scan-b"]}
    )
    assert resp.status_code == 201
    scan_id = resp.json()["scan_id"]

    result = await db.execute(select(ScanJob).where(ScanJob.scan_id == scan_id))
    merged = result.scalar_one()
    assert os.path.basename(merged.filepath).endswith("_merged.pdf")


async def test_collate_does_not_leave_tmp_pdf_next_to_source(db, user_client, tmp_path):
    """F46: the old code wrote `<source>.tmp.pdf` right next to the source
    scan file; the new per-invocation work dir must leave the source
    directory untouched."""
    await _seed_setting(db, "scan_dir", str(tmp_path))
    img1, img2 = await _seed_two_image_scans(db, tmp_path)

    resp = await user_client.post(
        "/api/scanner/collate", json={"scan_ids": ["scan-a", "scan-b"]}
    )
    assert resp.status_code == 201

    assert not os.path.exists(str(img1) + ".tmp.pdf")
    assert not os.path.exists(str(img2) + ".tmp.pdf")


async def test_collate_pdfs_sync_cleans_up_work_dir_on_failure(tmp_path):
    """Unit-level: a mid-merge failure (e.g. a corrupt/unreadable source
    image) must not leak the temporary work directory."""
    good = tmp_path / "good.png"
    Image.new("L", (50, 50), 255).save(good)
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not a real image")

    out_path = tmp_path / "out.pdf"
    tmp_dirs_before = {
        p for p in os.listdir(tmp_path) if os.path.isdir(os.path.join(tmp_path, p))
    }

    try:
        await asyncio.to_thread(
            _collate_pdfs_sync, [(str(good), 300), (str(bad), 300)], str(out_path)
        )
    except Exception:
        pass

    assert not out_path.exists()
    tmp_dirs_after = {
        p for p in os.listdir(tmp_path) if os.path.isdir(os.path.join(tmp_path, p))
    }
    assert tmp_dirs_after == tmp_dirs_before  # no leaked papyrus_collate_* dir
