"""eSCL (AirScan) API suite — capabilities/status gating and scan-job creation
through the real HTTP surface.

These routes are mounted at the ASGI root (no ``/api`` prefix, no auth — real
network scanners hit them directly), so tests use the bare ``client`` fixture
rather than ``admin_client``/``user_client``. ``escl_enabled`` is seeded as a
real AppConfig row (the same pattern as test_api_jobs.py's ``_seed_setting``)
rather than monkeypatched.

``POST /eSCL/ScanJobs`` fires ``asyncio.create_task(_run_scan(job_id))`` to
run the scan in the background. Left alone, that task would keep running
after the test body (and the ``db``/``client`` fixtures) have torn down,
racing the per-test TRUNCATE and potentially logging "Task was destroyed but
it is pending" once the event loop closes. The ``_captured_tasks`` fixture
wraps ``asyncio.create_task`` so every task the router spawns can be awaited
to completion before the test asserts anything and before it ends.
``scan_service``/``get_default_scanner_device`` are faked at the escl
module's own import sites so no real ``scanimage`` subprocess ever runs.

Module-level ``_scan_jobs`` is cleared before and after every test (mirrors
the autouse fixture in test_escl_job_eviction.py, scoped to this file).
"""
import asyncio

import pytest
from sqlalchemy import select

from app.models import AppConfig, ScanJob
from app.routers import escl
from app.services import settings_cache

_MINIMAL_SCAN_SETTINGS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03"
                    xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">
  <pwg:Version>2.6</pwg:Version>
  <scan:XResolution>150</scan:XResolution>
  <scan:YResolution>150</scan:YResolution>
  <scan:ColorMode>Grayscale8</scan:ColorMode>
  <pwg:DocumentFormat>application/pdf</pwg:DocumentFormat>
  <pwg:InputSource>Platen</pwg:InputSource>
</scan:ScanSettings>
"""


async def _seed_setting(db, key: str, value: str) -> None:
    db.add(AppConfig(key=key, value=value))
    await db.commit()
    settings_cache.invalidate_all()


async def _enable_escl(db) -> None:
    await _seed_setting(db, "escl_enabled", "true")


@pytest.fixture(autouse=True)
def _clear_scan_jobs():
    escl._scan_jobs.clear()
    yield
    escl._scan_jobs.clear()


@pytest.fixture
def _captured_tasks(monkeypatch):
    """Capture every ``asyncio.Task`` the eSCL router spawns via
    ``asyncio.create_task`` so a test can await it to completion instead of
    leaving it to run detached past the end of the test body."""
    tasks: list[asyncio.Task] = []
    real_create_task = asyncio.create_task

    def _capture(coro, *args, **kwargs):
        task = real_create_task(coro, *args, **kwargs)
        tasks.append(task)
        return task

    monkeypatch.setattr(escl.asyncio, "create_task", _capture)
    return tasks


class _FakeScanService:
    """Stand-in for the real ``ScanService`` singleton, patched at
    ``app.routers.escl.scan_service`` (not the module-level singleton
    itself, so nothing outside this test observes the fake)."""

    def __init__(self, filepath: str, *, error: Exception | None = None):
        self._lock = asyncio.Lock()
        self._filepath = filepath
        self._error = error
        self.calls: list[dict] = []

    async def scan(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return "fixture-scan-id", self._filepath


async def _fake_get_default_scanner_device(_db) -> str:
    return "test:device0"


def _patch_scan(monkeypatch, filepath: str, *, error: Exception | None = None) -> _FakeScanService:
    fake = _FakeScanService(filepath, error=error)
    monkeypatch.setattr(escl, "scan_service", fake)
    monkeypatch.setattr(escl, "get_default_scanner_device", _fake_get_default_scanner_device)
    return fake


# --------------------------------------------------------------------------- #
# GET /eSCL/ScannerCapabilities
# --------------------------------------------------------------------------- #
async def test_scanner_capabilities_disabled_returns_503(client):
    resp = await client.get("/eSCL/ScannerCapabilities")
    assert resp.status_code == 503


async def test_scanner_capabilities_enabled_returns_xml(db, client):
    await _enable_escl(db)

    resp = await client.get("/eSCL/ScannerCapabilities")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/xml")
    assert "<scan:ScannerCapabilities" in resp.text
    assert "<pwg:MakeAndModel>Papyrus Network Scanner</pwg:MakeAndModel>" in resp.text


# --------------------------------------------------------------------------- #
# GET /eSCL/ScannerStatus
# --------------------------------------------------------------------------- #
async def test_scanner_status_disabled_returns_503(client):
    resp = await client.get("/eSCL/ScannerStatus")
    assert resp.status_code == 503


async def test_scanner_status_enabled_returns_idle(db, client):
    await _enable_escl(db)

    resp = await client.get("/eSCL/ScannerStatus")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/xml")
    assert "<scan:ScannerStatus" in resp.text
    assert "<pwg:State>Idle</pwg:State>" in resp.text


# --------------------------------------------------------------------------- #
# POST /eSCL/ScanJobs
# --------------------------------------------------------------------------- #
async def test_create_scan_job_disabled_returns_503_without_creating_job(client):
    resp = await client.post("/eSCL/ScanJobs", content=_MINIMAL_SCAN_SETTINGS_XML)
    assert resp.status_code == 503
    assert escl._scan_jobs == {}


async def test_create_scan_job_runs_to_completion_and_persists_scan(
    db, client, tmp_path, monkeypatch, _captured_tasks
):
    await _enable_escl(db)

    scan_file = tmp_path / "fixture-scan-id.pdf"
    scan_file.write_bytes(b"%PDF-1.4 fake scan\n")
    _patch_scan(monkeypatch, str(scan_file))

    resp = await client.post("/eSCL/ScanJobs", content=_MINIMAL_SCAN_SETTINGS_XML)
    assert resp.status_code == 201
    location = resp.headers["location"]
    assert location.startswith("/eSCL/ScanJobs/")
    job_id = location.rsplit("/", 1)[-1]

    # Drain the background scan task deterministically instead of sleeping.
    await asyncio.gather(*_captured_tasks)

    assert escl._scan_jobs[job_id]["state"] == "Completed"
    assert escl._scan_jobs[job_id]["filepath"] == str(scan_file)

    await db.rollback()  # fresh snapshot — the background task committed on its own session
    result = await db.execute(select(ScanJob))
    jobs = result.scalars().all()
    assert len(jobs) == 1
    assert jobs[0].status == "completed"
    assert jobs[0].scan_id == "fixture-scan-id"
    assert jobs[0].filepath == str(scan_file)
    assert jobs[0].user_id is None  # network scan, no authenticated user


async def test_create_scan_job_scan_failure_marks_job_canceled_and_db_row_failed(
    db, client, monkeypatch, _captured_tasks
):
    await _enable_escl(db)
    _patch_scan(monkeypatch, "/unused/path", error=RuntimeError("scanner jammed"))

    resp = await client.post("/eSCL/ScanJobs", content=_MINIMAL_SCAN_SETTINGS_XML)
    assert resp.status_code == 201
    job_id = resp.headers["location"].rsplit("/", 1)[-1]

    await asyncio.gather(*_captured_tasks)

    assert escl._scan_jobs[job_id]["state"] == "Canceled"
    assert "scanner jammed" in escl._scan_jobs[job_id]["error"]

    await db.rollback()
    result = await db.execute(select(ScanJob))
    jobs = result.scalars().all()
    assert len(jobs) == 1
    assert jobs[0].status == "failed"
    assert "scanner jammed" in jobs[0].error_message
