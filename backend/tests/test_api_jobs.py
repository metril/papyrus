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

import pytest

from app.models import AppConfig, Printer
from app.routers import jobs as jobs_router
from app.services import settings_cache

_MINIMAL_PDF = b"%PDF-1.4\n1 0 obj\n<< >>\nendobj\ntrailer\n<< >>\n%%EOF\n"


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


@pytest.mark.xfail(
    strict=True,
    reason=(
        "app bug (not a test bug): _process_job (app/routers/jobs.py) commits "
        "job.status = 'printing' and immediately broadcasts serialize_print_job(job) "
        "with no intervening `await db.refresh(job)`. PrintJob.updated_at has "
        "onupdate=func.now() (a server-side default), so after that UPDATE "
        "SQLAlchemy marks `updated_at` expired; the Pydantic serialization in "
        "serialize_print_job reads it synchronously and raises "
        "sqlalchemy.exc.MissingGreenlet, well before create_held_job/release_job "
        "are ever called. The blanket `except Exception` then marks the job "
        "'failed' and retries the identical broadcast, which crashes the same "
        "way -- uncaught this time, so the endpoint 500s instead of completing. "
        "release_job() avoids this because it calls db.refresh(job) right after "
        "its own commit, before broadcasting; _process_job does not. Affects both "
        "callers of _process_job: hold=false uploads and auto_release network "
        "ingestion. Remove this marker once app code adds the missing refresh "
        "(or enables eager_defaults) -- see p5-task-4-report.md."
    ),
)
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
