"""eSCL feeder jobs on devices that take every sheet at once: non-PDF formats
are held per job and served one NextDocument at a time. These tests use the
shared DB harness and the real eSCL routes; only the scanner backend is fake."""
import asyncio
import io
import os
import re
import sys

import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import select

from app.models import AppConfig, ScanJob
from app.routers import escl
from app.services import scan_service as ss
from app.services import settings_cache
from tests.test_scanners_native_api import _add

XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03"
                    xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">
  <scan:XResolution>300</scan:XResolution>
  <scan:ColorMode>RGB24</scan:ColorMode>
  <pwg:DocumentFormat>{fmt}</pwg:DocumentFormat>
  <pwg:InputSource>{src}</pwg:InputSource>
</scan:ScanSettings>
"""


class _ConnectedRequest:
    async def is_disconnected(self):
        return False


class FakeBackend:
    def __init__(self, pages=3, hold=False):
        self.pages = pages
        self.hold = hold

    async def acquire_batch(self, *, page_dir, **kw):
        if self.hold:
            await asyncio.sleep(3600)
        for n in range(1, self.pages + 1):
            Image.new("RGB", (30 * n, 40), (n * 40, 0, 0)).save(
                os.path.join(page_dir, f"page_{n:04d}.tiff"), dpi=(300, 300)
            )

    async def acquire_page(self, *, tiff_file, **kw):
        Image.new("RGB", (30, 40)).save(tiff_file)


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path, real_img2pdf):
    escl._scan_jobs.clear()
    backend = FakeBackend()

    async def allow(_h):
        return None

    monkeypatch.setattr(ss, "assert_safe_host", allow)
    monkeypatch.setattr(ss.ScanService, "_backend_for", lambda self, d: backend)
    yield backend
    escl._scan_jobs.clear()


@pytest.fixture
def real_img2pdf(monkeypatch):
    pytest.importorskip("img2pdf")
    real_exec = asyncio.create_subprocess_exec

    async def exec_(*args, **kw):
        if args[0] == "img2pdf":
            args = (sys.executable, "-m", "img2pdf", *args[1:])
        return await real_exec(*args, **kw)

    monkeypatch.setattr(ss.asyncio, "create_subprocess_exec", exec_)


@pytest_asyncio.fixture(loop_scope="function")
async def setup(db, tmp_path, monkeypatch):
    db.add(AppConfig(key="escl_enabled", value="true"))
    db.add(AppConfig(key="scan_dir", value=str(tmp_path)))
    await db.commit()
    settings_cache.invalidate_all()
    return tmp_path


async def _start(client, db, device, fmt, src="Feeder"):
    await _add(db, device=device)
    resp = await client.post(
        "/eSCL/ScanJobs", content=XML.decode().format(fmt=fmt, src=src).encode()
    )
    assert resp.status_code == 201
    job_id = resp.headers["Location"].rsplit("/", 1)[1]
    return job_id


async def _finish(job_id):
    await escl._scan_jobs[job_id]["task"]


def _files(path):
    return sorted(os.listdir(path))


@pytest.mark.parametrize("device", ["brothernet:127.0.0.1", "brother4:net1;dev0"])
async def test_pages_served_in_order_then_404(client, db, setup, device):
    job_id = await _start(client, db, device, "image/jpeg")
    await _finish(job_id)

    widths = []
    for _ in range(3):
        r = await client.get(f"/eSCL/ScanJobs/{job_id}/NextDocument")
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
        widths.append(Image.open(io.BytesIO(r.content)).size[0])
    assert widths == [30, 60, 90]
    assert (await client.get(f"/eSCL/ScanJobs/{job_id}/NextDocument")).status_code == 404
    assert (await client.get(f"/eSCL/ScanJobs/{job_id}/NextDocument")).status_code == 404
    await asyncio.sleep(0.05)
    assert [f for f in _files(setup) if f.startswith("pages_")] == []
    row = (await db.execute(select(ScanJob))).scalars().first()
    await db.refresh(row)
    assert row.page_count == 3 and row.format == "pdf" and row.status == "completed"
    assert os.path.exists(row.filepath) and row.filepath.endswith(".pdf")


async def test_status_stays_processing_with_counts_until_last_page(client, db, setup):
    job_id = await _start(client, db, "brothernet:127.0.0.1", "image/png")
    await _finish(job_id)

    async def info():
        text = (await client.get("/eSCL/ScannerStatus")).text
        return (
            re.search(r"<pwg:JobState>(\w+)<", text).group(1),
            re.search(r"ImagesCompleted>(\d+)<", text).group(1),
            re.search(r"ImagesToTransfer>(\d+)<", text).group(1),
        )

    assert await info() == ("Processing", "0", "3")
    await client.get(f"/eSCL/ScanJobs/{job_id}/NextDocument")
    assert await info() == ("Processing", "1", "2")
    await client.get(f"/eSCL/ScanJobs/{job_id}/NextDocument")
    await client.get(f"/eSCL/ScanJobs/{job_id}/NextDocument")
    assert await info() == ("Completed", "3", "0")


async def test_single_sheet_stack(client, db, setup, env):
    env.pages = 1
    job_id = await _start(client, db, "brothernet:127.0.0.1", "image/jpeg")
    await _finish(job_id)
    assert (await client.get(f"/eSCL/ScanJobs/{job_id}/NextDocument")).status_code == 200
    assert (await client.get(f"/eSCL/ScanJobs/{job_id}/NextDocument")).status_code == 404


async def test_delete_mid_stack_removes_everything_pages(client, db, setup):
    job_id = await _start(client, db, "brothernet:127.0.0.1", "image/jpeg")
    await _finish(job_id)
    await client.get(f"/eSCL/ScanJobs/{job_id}/NextDocument")
    assert any(f.startswith("pages_") for f in _files(setup))
    assert (await client.delete(f"/eSCL/ScanJobs/{job_id}")).status_code == 200
    assert not any(f.startswith("pages_") for f in _files(setup))
    assert (await client.get(f"/eSCL/ScanJobs/{job_id}/NextDocument")).status_code == 404


async def test_delete_while_scanning_leaks_nothing(client, db, setup, env):
    env.hold = True
    job_id = await _start(client, db, "brothernet:127.0.0.1", "image/jpeg")
    await asyncio.sleep(0.3)
    assert (await client.delete(f"/eSCL/ScanJobs/{job_id}")).status_code == 200
    assert _files(setup) == []


async def test_failed_scan_leaves_no_files(client, db, setup, env):
    env.pages = 0
    job_id = await _start(client, db, "brothernet:127.0.0.1", "image/jpeg")
    await _finish(job_id)
    assert escl._scan_jobs[job_id]["state"] == "Canceled"
    assert _files(setup) == []


async def test_abandoned_job_expires_and_files_go(client, db, setup, monkeypatch):
    job_id = await _start(client, db, "brothernet:127.0.0.1", "image/jpeg")
    await _finish(job_id)
    await client.get(f"/eSCL/ScanJobs/{job_id}/NextDocument")
    escl._scan_jobs[job_id]["terminal_at"] -= escl._JOB_TTL_SECONDS + 1
    await client.get("/eSCL/ScannerStatus")
    assert job_id not in escl._scan_jobs
    assert not any(f.startswith("pages_") for f in _files(setup))


async def test_pdf_stays_one_document(client, db, setup):
    job_id = await _start(client, db, "brothernet:127.0.0.1", "application/pdf")
    await _finish(job_id)
    r = await client.get(f"/eSCL/ScanJobs/{job_id}/NextDocument")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    assert (await client.get(f"/eSCL/ScanJobs/{job_id}/NextDocument")).status_code == 404
    assert "ImagesCompleted" not in (await client.get("/eSCL/ScannerStatus")).text


async def test_flatbed_unchanged(client, db, setup):
    job_id = await _start(client, db, "brothernet:127.0.0.1", "image/jpeg", src="Platen")
    await _finish(job_id)
    job = escl._scan_jobs[job_id]
    assert job["held"] is False and job["state"] == "Completed"
    r = await client.get(f"/eSCL/ScanJobs/{job_id}/NextDocument")
    assert r.status_code == 200
    assert (await client.get(f"/eSCL/ScanJobs/{job_id}/NextDocument")).status_code == 404


async def test_page_stays_queued_until_send_completes_and_is_reserved_after_failure(
    client, db, setup
):
    job_id = await _start(client, db, "brothernet:127.0.0.1", "image/jpeg")
    await _finish(job_id)
    job = escl._scan_jobs[job_id]
    first = job["pages"][0]

    # A send that never finishes: the response is built but its background
    # task (the delete) never runs.
    await escl.get_next_document(job_id, _ConnectedRequest())
    assert job["pages"][0] == first and os.path.exists(first)
    assert len(job["pages"]) == 3

    r = await client.get(f"/eSCL/ScanJobs/{job_id}/NextDocument")
    assert r.status_code == 200 and Image.open(io.BytesIO(r.content)).size[0] == 30
    await asyncio.sleep(0.05)
    assert len(job["pages"]) == 2 and not os.path.exists(first)


async def test_superseded_send_does_not_delete_the_page(client, db, setup):
    job_id = await _start(client, db, "brothernet:127.0.0.1", "image/jpeg")
    await _finish(job_id)
    job = escl._scan_jobs[job_id]
    old = await escl.get_next_document(job_id, _ConnectedRequest())
    await escl.get_next_document(job_id, _ConnectedRequest())
    await old.background()
    assert len(job["pages"]) == 3


async def test_held_pages_are_fitted_to_the_requested_region(client, db, setup):
    await _add(db, device="brothernet:127.0.0.1")
    xml = XML.decode().format(fmt="image/jpeg", src="Feeder").replace(
        "</scan:ScanSettings>",
        "<pwg:ScanRegions><pwg:ScanRegion><pwg:Width>300</pwg:Width>"
        "<pwg:Height>450</pwg:Height><pwg:XOffset>0</pwg:XOffset><pwg:YOffset>0</pwg:YOffset>"
        "</pwg:ScanRegion></pwg:ScanRegions></scan:ScanSettings>",
    )
    resp = await client.post("/eSCL/ScanJobs", content=xml.encode())
    job_id = resp.headers["Location"].rsplit("/", 1)[1]
    await _finish(job_id)
    sizes = []
    for _ in range(3):
        r = await client.get(f"/eSCL/ScanJobs/{job_id}/NextDocument")
        sizes.append(Image.open(io.BytesIO(r.content)).size)
    assert sizes == [(300, 450)] * 3


async def test_unowned_output_is_removed_when_post_processing_fails(
    client, db, setup, monkeypatch
):
    await _add(db, device="brothernet:127.0.0.1")

    def boom(*a, **k):
        raise RuntimeError("resize")

    monkeypatch.setattr(escl, "_fit_image", boom)
    xml = XML.decode().format(fmt="image/jpeg", src="Platen").replace(
        "</scan:ScanSettings>",
        "<pwg:ScanRegions><pwg:ScanRegion><pwg:Width>300</pwg:Width>"
        "<pwg:Height>450</pwg:Height><pwg:XOffset>0</pwg:XOffset><pwg:YOffset>0</pwg:YOffset>"
        "</pwg:ScanRegion></pwg:ScanRegions></scan:ScanSettings>",
    )
    resp = await client.post("/eSCL/ScanJobs", content=xml.encode())
    job_id = resp.headers["Location"].rsplit("/", 1)[1]
    await _finish(job_id)
    assert escl._scan_jobs[job_id]["state"] == "Canceled"
    assert _files(setup) == []
