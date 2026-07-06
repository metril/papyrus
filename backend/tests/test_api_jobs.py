"""Job lifecycle API suite — upload, PIN handling, oversize rejection,
cancel/delete/bulk-delete, and network job ingest.

Goes through the ASGI app end-to-end (unlike test_cups_service.py's direct
calls). CupsService is faked at the router's own import site
(``app.routers.jobs.CupsService``) — the same class the existing unit tests
fake, just patched where ``jobs.py`` looks it up so the fake takes effect for
requests routed through HTTP.

``upload_dir``/``max_upload_size_mb``/``require_release_pin`` are seeded as
real AppConfig rows (committed via the ``db`` fixture, with
``settings_cache.invalidate_all()`` after each seed) rather than monkeypatched
— the same mechanism test_api_settings.py uses for settings reads/writes.
"""
import io
import os
import shutil

import pytest
from PIL import Image

from app.exceptions import ExternalServiceError
from app.models import AppConfig, Printer, PrintJob
from app.routers import jobs as jobs_router
from app.services import settings_cache

_MINIMAL_PDF = b"%PDF-1.4\n1 0 obj\n<< >>\nendobj\ntrailer\n<< >>\n%%EOF\n"


def _real_pdf_bytes(size: tuple[int, int] = (850, 1100), color=(20, 120, 200)) -> bytes:
    """A syntactically real single-page PDF that ghostscript can render — unlike
    `_MINIMAL_PDF`, which is just enough bytes to pass upload/mime sniffing."""
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="PDF")
    return buf.getvalue()


async def _seed_setting(db, key: str, value: str) -> None:
    db.add(AppConfig(key=key, value=value))
    await db.commit()
    settings_cache.invalidate_all()


async def _seed_upload_dir(db, tmp_path) -> None:
    await _seed_setting(db, "upload_dir", str(tmp_path))


def _pdf_file(name: str = "test.pdf", data: bytes = _MINIMAL_PDF) -> dict:
    return {"file": (name, io.BytesIO(data), "application/pdf")}


class _FakeCupsService:
    """Stand-in for CupsService, patched at ``app.routers.jobs.CupsService``.

    Records calls so tests can assert create/release/cancel happened without
    touching pycups.
    """

    last_instance: "_FakeCupsService | None" = None

    def __init__(self, printer_name: str = "") -> None:
        self.printer_name = printer_name
        self.created: list[tuple] = []
        self.released: list[int] = []
        self.cancelled: list[int] = []
        _FakeCupsService.last_instance = self

    async def create_held_job(self, filepath, title, copies=1, duplex=False, media="A4"):
        self.created.append((filepath, title, copies, duplex, media))
        return 777

    async def release_job(self, job_id):
        self.released.append(job_id)

    async def cancel_job(self, job_id):
        self.cancelled.append(job_id)


# --------------------------------------------------------------------------- #
# Upload
# --------------------------------------------------------------------------- #
async def test_upload_creates_held_job_with_file_on_disk(db, user_client, tmp_path):
    await _seed_upload_dir(db, tmp_path)

    resp = await user_client.post("/api/jobs/upload", files=_pdf_file())
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "held"
    assert body["filename"] == "test.pdf"
    assert "release_pin" not in body

    on_disk = list(tmp_path.iterdir())
    assert len(on_disk) == 1
    assert on_disk[0].name.endswith("_test.pdf")

    get_resp = await user_client.get(f"/api/jobs/{body['id']}")
    assert get_resp.status_code == 200
    assert get_resp.json()["status"] == "held"


async def test_upload_with_required_pin_setting_returns_generated_pin(db, user_client, tmp_path):
    await _seed_upload_dir(db, tmp_path)
    await _seed_setting(db, "require_release_pin", "true")

    resp = await user_client.post("/api/jobs/upload", files=_pdf_file())
    assert resp.status_code == 201
    body = resp.json()
    assert "release_pin" in body
    assert len(body["release_pin"]) == 4
    assert body["release_pin"].isdigit()
    assert body["has_pin"] is True


# --------------------------------------------------------------------------- #
# Release
# --------------------------------------------------------------------------- #
async def test_release_with_wrong_pin_is_403(db, user_client, tmp_path, monkeypatch):
    await _seed_upload_dir(db, tmp_path)
    monkeypatch.setattr(jobs_router, "CupsService", _FakeCupsService)

    upload_resp = await user_client.post(
        "/api/jobs/upload", files=_pdf_file(), data={"release_pin": "1234"}
    )
    job_id = upload_resp.json()["id"]

    release_resp = await user_client.post(f"/api/jobs/{job_id}/release", json={"pin": "0000"})
    assert release_resp.status_code == 403

    # Rejected release must not have touched CUPS or changed status.
    get_resp = await user_client.get(f"/api/jobs/{job_id}")
    assert get_resp.json()["status"] == "held"


async def test_release_with_correct_pin_prints_job(db, user_client, tmp_path, monkeypatch):
    await _seed_upload_dir(db, tmp_path)
    monkeypatch.setattr(jobs_router, "CupsService", _FakeCupsService)

    upload_resp = await user_client.post(
        "/api/jobs/upload", files=_pdf_file(), data={"release_pin": "1234"}
    )
    job_id = upload_resp.json()["id"]

    release_resp = await user_client.post(f"/api/jobs/{job_id}/release", json={"pin": "1234"})
    assert release_resp.status_code == 200
    body = release_resp.json()
    assert body["status"] == "printing"
    assert body["cups_job_id"] == 777

    fake = _FakeCupsService.last_instance
    assert fake is not None
    assert fake.created  # create_held_job was invoked
    assert fake.released == [777]


# --------------------------------------------------------------------------- #
# Oversize upload
# --------------------------------------------------------------------------- #
async def test_oversize_upload_is_413_with_no_partial_file(db, user_client, tmp_path):
    await _seed_upload_dir(db, tmp_path)
    await _seed_setting(db, "max_upload_size_mb", "1")

    oversized = b"0" * (2 * 1024 * 1024)  # 2 MiB > 1 MiB cap
    resp = await user_client.post(
        "/api/jobs/upload",
        files={"file": ("big.pdf", io.BytesIO(oversized), "application/pdf")},
    )
    assert resp.status_code == 413
    assert list(tmp_path.iterdir()) == []


# --------------------------------------------------------------------------- #
# Cancel / delete / bulk-delete
# --------------------------------------------------------------------------- #
async def test_cancel_job_sets_cancelled_status(db, user_client, tmp_path):
    await _seed_upload_dir(db, tmp_path)
    upload_resp = await user_client.post("/api/jobs/upload", files=_pdf_file())
    job_id = upload_resp.json()["id"]

    cancel_resp = await user_client.post(f"/api/jobs/{job_id}/cancel")
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "cancelled"


async def test_delete_job_removes_row(db, user_client, tmp_path):
    await _seed_upload_dir(db, tmp_path)
    upload_resp = await user_client.post("/api/jobs/upload", files=_pdf_file())
    job_id = upload_resp.json()["id"]

    delete_resp = await user_client.delete(f"/api/jobs/{job_id}")
    assert delete_resp.status_code == 204

    get_resp = await user_client.get(f"/api/jobs/{job_id}")
    assert get_resp.status_code == 404


async def test_bulk_delete_removes_all_rows(db, user_client, tmp_path):
    await _seed_upload_dir(db, tmp_path)
    ids = []
    for i in range(3):
        upload_resp = await user_client.post(
            "/api/jobs/upload", files=_pdf_file(f"job{i}.pdf")
        )
        ids.append(upload_resp.json()["id"])

    bulk_resp = await user_client.post("/api/jobs/bulk-delete", json={"ids": ids})
    assert bulk_resp.status_code == 200
    assert bulk_resp.json()["deleted"] == 3

    for job_id in ids:
        get_resp = await user_client.get(f"/api/jobs/{job_id}")
        assert get_resp.status_code == 404


# --------------------------------------------------------------------------- #
# Network job ingest
# --------------------------------------------------------------------------- #
async def test_ingest_network_job_from_localhost_is_held(db, client, tmp_path):
    # ASGITransport reports the client host as 127.0.0.1 by default, matching
    # the localhost-only guard on this internal endpoint.
    await _seed_upload_dir(db, tmp_path)

    resp = await client.post(
        "/api/jobs/internal/ingest",
        files=_pdf_file("network.pdf"),
        data={"title": "Network Job", "username": "someone"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "held"
    assert body["source_type"] == "network"


# --------------------------------------------------------------------------- #
# print.held webhook dispatch
# --------------------------------------------------------------------------- #
def _capture_held(monkeypatch) -> list:
    """Patch jobs_router.dispatch_webhook to record (event, data) tuples."""
    events: list = []

    async def fake_dispatch(_db, event, data):
        events.append((event, data))

    monkeypatch.setattr(jobs_router, "dispatch_webhook", fake_dispatch)
    return events


async def test_upload_held_job_dispatches_print_held(db, user_client, tmp_path, monkeypatch):
    await _seed_upload_dir(db, tmp_path)
    events = _capture_held(monkeypatch)

    resp = await user_client.post("/api/jobs/upload", files=_pdf_file())
    assert resp.status_code == 201

    held = [d for e, d in events if e == "print.held"]
    assert len(held) == 1
    assert held[0]["source_type"] == "upload"
    assert held[0]["id"] == resp.json()["id"]
    assert "user_id" in held[0]


async def test_upload_not_held_does_not_dispatch_print_held(db, user_client, tmp_path, monkeypatch):
    await _seed_upload_dir(db, tmp_path)
    monkeypatch.setattr(jobs_router, "CupsService", _FakeCupsService)
    events = _capture_held(monkeypatch)

    resp = await user_client.post("/api/jobs/upload", files=_pdf_file(), data={"hold": "false"})
    assert resp.status_code == 201

    assert [e for e, _ in events if e == "print.held"] == []


async def test_ingest_held_network_job_dispatches_print_held(db, client, tmp_path, monkeypatch):
    await _seed_upload_dir(db, tmp_path)
    events = _capture_held(monkeypatch)

    resp = await client.post(
        "/api/jobs/internal/ingest",
        files=_pdf_file("network.pdf"),
        data={"username": "someone"},
    )
    assert resp.status_code == 201

    held = [d for e, d in events if e == "print.held"]
    assert len(held) == 1
    assert held[0]["source_type"] == "network"
    assert held[0]["username"] == "someone"


async def test_ingest_auto_release_does_not_dispatch_print_held(db, client, tmp_path, monkeypatch):
    await _seed_upload_dir(db, tmp_path)
    monkeypatch.setattr(jobs_router, "CupsService", _FakeCupsService)
    events = _capture_held(monkeypatch)

    printer = Printer(
        display_name="Auto", cups_name="auto", uri="",
        is_default=True, is_network_queue=False, auto_release=True,
    )
    db.add(printer)
    await db.commit()

    resp = await client.post("/api/jobs/internal/ingest", files=_pdf_file("auto.pdf"))
    assert resp.status_code == 201
    assert resp.json()["status"] == "completed"
    # Auto-released jobs skip the hold queue -> no print.held.
    assert [e for e, _ in events if e == "print.held"] == []


# Regression test: _process_job used to broadcast serialize_print_job(job)
# right after commit without db.refresh(job); the server-side updated_at was
# expired by the UPDATE flush and the synchronous serialization raised
# MissingGreenlet, 500ing every hold=false upload and auto_release ingest.
async def test_ingest_network_job_with_auto_release_printer_completes(
    db, client, tmp_path, monkeypatch
):
    await _seed_upload_dir(db, tmp_path)
    monkeypatch.setattr(jobs_router, "CupsService", _FakeCupsService)

    printer = Printer(
        display_name="Auto",
        cups_name="auto",
        uri="",
        is_default=True,
        is_network_queue=False,
        auto_release=True,
    )
    db.add(printer)
    await db.commit()

    resp = await client.post(
        "/api/jobs/internal/ingest",
        files=_pdf_file("auto.pdf"),
    )
    assert resp.status_code == 201
    body = resp.json()
    # _process_job runs to completion inline: held -> printing -> completed.
    assert body["status"] == "completed"
    assert body["cups_job_id"] == 777

    fake = _FakeCupsService.last_instance
    assert fake is not None
    assert fake.released == [777]


# --------------------------------------------------------------------------- #
# Thumbnail endpoint (GET /{job_id}/thumbnail)
# --------------------------------------------------------------------------- #
async def test_job_thumbnail_404_when_job_missing(user_client):
    resp = await user_client.get("/api/jobs/999999/thumbnail")
    assert resp.status_code == 404


@pytest.mark.skipif(shutil.which("gs") is None, reason="ghostscript not installed")
async def test_pdf_job_thumbnail_returns_jpeg_and_is_cached_on_repeat(
    db, user_client, tmp_path
):
    await _seed_upload_dir(db, tmp_path)
    upload_resp = await user_client.post(
        "/api/jobs/upload", files=_pdf_file(data=_real_pdf_bytes())
    )
    job_id = upload_resp.json()["id"]

    resp = await user_client.get(f"/api/jobs/{job_id}/thumbnail")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.headers["cache-control"] == "private, max-age=86400"

    thumb_file = next(f for f in tmp_path.iterdir() if f.name.endswith(".thumb.jpg"))
    first_mtime = thumb_file.stat().st_mtime

    # Repeat request must reuse the cached .thumb.jpg, not regenerate it —
    # get_or_create_thumbnail's mtime check short-circuits regeneration.
    resp2 = await user_client.get(f"/api/jobs/{job_id}/thumbnail")
    assert resp2.status_code == 200
    assert thumb_file.stat().st_mtime == first_mtime


@pytest.mark.skipif(shutil.which("gs") is None, reason="ghostscript not installed")
async def test_office_job_thumbnail_converts_and_caches_preview_pdf(
    db, user_client, tmp_path, monkeypatch
):
    """The thumbnail endpoint shares `_ensure_preview_pdf` with `/preview`: an
    office-doc job gets converted to PDF (cached as `.preview.pdf`) before
    being thumbnailed. `convert_to_pdf` itself is faked here — exercising real
    LibreOffice is `test_convert_service.py`'s job — but it writes a real
    single-page PDF so the ghostscript thumbnail render underneath is real.
    """
    doc_path = tmp_path / "report.docx"
    doc_path.write_bytes(b"not a real docx; convert_to_pdf is faked below")

    job = PrintJob(
        title="report.docx",
        filename="report.docx",
        filepath=str(doc_path),
        file_size=doc_path.stat().st_size,
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        status="held",
        source_type="upload",
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    converted_path = tmp_path / "converted_output.pdf"
    converted_path.write_bytes(_real_pdf_bytes())

    calls = []

    async def _fake_convert_to_pdf(input_path, output_dir):
        calls.append((input_path, output_dir))
        return str(converted_path)

    monkeypatch.setattr(jobs_router, "convert_to_pdf", _fake_convert_to_pdf)

    resp = await user_client.get(f"/api/jobs/{job.id}/thumbnail")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.headers["cache-control"] == "private, max-age=86400"
    assert calls == [(str(doc_path), str(tmp_path))]

    preview_path = str(doc_path) + ".preview.pdf"
    assert os.path.exists(preview_path)  # cached for reuse by /preview too
    assert os.path.exists(preview_path + ".thumb.jpg")
    assert not os.path.exists(converted_path)  # renamed into the cache, not copied


# --------------------------------------------------------------------------- #
# _ensure_preview_pdf unit tests — plain job-like objects, no DB/HTTP needed
# --------------------------------------------------------------------------- #
class _JobStub:
    def __init__(self, mime_type: str, filepath: str):
        self.mime_type = mime_type
        self.filepath = filepath


async def _unreachable_convert_to_pdf(*args, **kwargs):
    raise AssertionError("convert_to_pdf must not be called for this branch")


async def test_ensure_preview_pdf_passes_through_pdf_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_router, "convert_to_pdf", _unreachable_convert_to_pdf)
    job = _JobStub(mime_type="application/pdf", filepath=str(tmp_path / "a.pdf"))

    result = await jobs_router._ensure_preview_pdf(job)

    assert result == job.filepath


async def test_ensure_preview_pdf_passes_through_image_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_router, "convert_to_pdf", _unreachable_convert_to_pdf)
    job = _JobStub(mime_type="image/jpeg", filepath=str(tmp_path / "a.jpg"))

    result = await jobs_router._ensure_preview_pdf(job)

    assert result == job.filepath


async def test_ensure_preview_pdf_converts_office_doc_and_caches_result(tmp_path, monkeypatch):
    src = tmp_path / "doc.docx"
    src.write_bytes(b"fake docx")
    converted = tmp_path / "doc.pdf"
    converted.write_bytes(b"%PDF-fake-converted%")

    calls = []

    async def _fake_convert(input_path, output_dir):
        calls.append((input_path, output_dir))
        return str(converted)

    monkeypatch.setattr(jobs_router, "convert_to_pdf", _fake_convert)
    job = _JobStub(
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filepath=str(src),
    )

    result = await jobs_router._ensure_preview_pdf(job)

    expected_preview = str(src) + ".preview.pdf"
    assert result == expected_preview
    assert os.path.exists(expected_preview)
    assert not os.path.exists(converted)  # renamed, not copied
    assert calls == [(str(src), str(tmp_path))]


async def test_ensure_preview_pdf_reuses_cached_preview_without_reconverting(
    tmp_path, monkeypatch
):
    src = tmp_path / "doc.docx"
    src.write_bytes(b"fake docx")
    preview_path = str(src) + ".preview.pdf"
    with open(preview_path, "wb") as f:
        f.write(b"already-cached")

    monkeypatch.setattr(jobs_router, "convert_to_pdf", _unreachable_convert_to_pdf)
    job = _JobStub(
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filepath=str(src),
    )

    result = await jobs_router._ensure_preview_pdf(job)

    assert result == preview_path


async def test_ensure_preview_pdf_wraps_conversion_failure(tmp_path, monkeypatch):
    src = tmp_path / "doc.docx"
    src.write_bytes(b"fake docx")

    async def _fail(*args, **kwargs):
        raise RuntimeError("libreoffice exploded")

    monkeypatch.setattr(jobs_router, "convert_to_pdf", _fail)
    job = _JobStub(
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filepath=str(src),
    )

    with pytest.raises(ExternalServiceError):
        await jobs_router._ensure_preview_pdf(job)
