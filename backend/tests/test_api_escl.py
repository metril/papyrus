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
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.main import app
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

    def is_busy(self) -> bool:
        return self._lock.locked()

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


@pytest.fixture
async def public_client():
    """Same ASGI app as ``client``, but with a public (non-LAN) source
    address — for exercising F3's ``_require_lan_client`` rejection. The
    default ``client``/``ASGITransport`` fixture always presents
    ``127.0.0.1`` (loopback), which F3 must allow, so a distinct transport
    is needed to simulate a non-LAN caller. Uses a real, globally-routable
    address (Google Public DNS) rather than an RFC 5737 documentation
    address (e.g. 203.0.113.0/24) -- Python's ``ipaddress.is_private``
    treats those reserved/non-routable ranges as private too, which would
    make the test pass for the wrong reason."""
    transport = ASGITransport(app=app, client=("8.8.8.8", 12345))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


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


# --------------------------------------------------------------------------- #
# F3: LAN-only source restriction + busy-reject
# --------------------------------------------------------------------------- #
async def test_public_source_ip_is_403_on_capabilities(public_client):
    resp = await public_client.get("/eSCL/ScannerCapabilities")
    assert resp.status_code == 403


async def test_public_source_ip_is_403_on_create_scan_job(db, public_client):
    await _enable_escl(db)
    resp = await public_client.post("/eSCL/ScanJobs", content=_MINIMAL_SCAN_SETTINGS_XML)
    assert resp.status_code == 403
    assert escl._scan_jobs == {}


async def test_loopback_source_ip_is_allowed(client):
    # Sanity check: the default `client` fixture presents 127.0.0.1
    # (loopback) and must NOT be rejected by F3.
    resp = await client.get("/eSCL/ScannerCapabilities")
    assert resp.status_code == 503  # disabled, not 403 -- LAN check passed


# --------------------------------------------------------------------------- #
# F3 coordinator ruling: the deployment fronts the whole app with Traefik on
# the same host (network_mode: host), so request.client.host is always
# loopback/private for every proxied request -- the LAN check alone can
# never tell a real LAN caller from one relayed from the internet. When the
# TCP peer is loopback/private AND an X-Forwarded-For header is present, the
# *rightmost* XFF entry (appended by the single trusted proxy hop) is
# evaluated instead of the peer; with no XFF, the peer (already known LAN)
# is used as before. A non-LAN peer is never trusted to supply XFF at all.
# --------------------------------------------------------------------------- #
async def test_xff_public_rightmost_behind_trusted_peer_is_403(client):
    # A real, globally-routable address (Google Public DNS), not an RFC 5737
    # documentation address (e.g. 203.0.113.0/24) -- see public_client's own
    # docstring: Python's ipaddress.is_private treats those reserved,
    # non-routable ranges as private too, which would make this pass for
    # the wrong reason.
    resp = await client.get(
        "/eSCL/ScannerCapabilities",
        headers={"X-Forwarded-For": "8.8.8.8"},
    )
    assert resp.status_code == 403


async def test_xff_private_rightmost_behind_trusted_peer_is_allowed(client):
    # Rightmost entry (192.168.1.20) is private -- allowed, regardless of
    # what the client-supplied earlier hop (1.2.3.4) claims.
    resp = await client.get(
        "/eSCL/ScannerCapabilities",
        headers={"X-Forwarded-For": "1.2.3.4, 192.168.1.20"},
    )
    assert resp.status_code == 503  # disabled, not 403 -- LAN check passed


async def test_xff_from_untrusted_public_peer_is_ignored_and_still_403(public_client):
    # The peer itself (8.8.8.8, from the public_client fixture) isn't
    # loopback/private, so it's never trusted to supply XFF at all -- a
    # private-looking XFF value must not let it through.
    resp = await public_client.get(
        "/eSCL/ScannerCapabilities",
        headers={"X-Forwarded-For": "192.168.1.20"},
    )
    assert resp.status_code == 403


async def test_no_xff_header_private_peer_is_allowed(client):
    resp = await client.get("/eSCL/ScannerCapabilities")
    assert resp.status_code == 503  # disabled, not 403 -- LAN check passed


async def test_second_concurrent_scan_job_is_503(db, client, monkeypatch, _captured_tasks):
    await _enable_escl(db)

    # Seed an already-in-flight eSCL job (Processing) so the busy check trips
    # without needing a real overlapping scan.
    escl._scan_jobs["already-running"] = {
        "state": "Processing",
        "resolution": 300,
        "color_mode": "Color",
        "format": "pdf",
        "source": "Flatbed",
        "scan_region": {},
        "filepath": None,
        "served": False,
        "error": None,
        "terminal_at": None,
    }

    resp = await client.post("/eSCL/ScanJobs", content=_MINIMAL_SCAN_SETTINGS_XML)
    assert resp.status_code == 503
    # No second job was created.
    assert set(escl._scan_jobs.keys()) == {"already-running"}


async def test_scan_service_busy_rejects_new_job(db, client, monkeypatch):
    await _enable_escl(db)

    class _BusyScanService:
        def is_busy(self) -> bool:
            return True

    monkeypatch.setattr(escl, "scan_service", _BusyScanService())

    resp = await client.post("/eSCL/ScanJobs", content=_MINIMAL_SCAN_SETTINGS_XML)
    assert resp.status_code == 503
    assert escl._scan_jobs == {}


# --------------------------------------------------------------------------- #
# F4/F135: resolution snapping + region clamping
# --------------------------------------------------------------------------- #
def test_snap_resolution_picks_nearest_advertised_value():
    assert escl._snap_resolution(290) == 300
    assert escl._snap_resolution(80) == 75
    assert escl._snap_resolution(10_000) == 600


def test_clamp_region_full_bed_when_height_missing():
    """F135: Width without Height must fall back to a full-bed scan instead
    of storing a half-filled region that crashes _run_scan's mm math."""
    region = {"width": 1000, "height": None, "x_offset": 0, "y_offset": 0}
    clamped = escl._clamp_scan_region(region)
    assert clamped == {"width": None, "height": None, "x_offset": 0, "y_offset": 0}


def test_clamp_region_full_bed_when_width_missing():
    region = {"width": None, "height": 1000, "x_offset": 0, "y_offset": 0}
    clamped = escl._clamp_scan_region(region)
    assert clamped == {"width": None, "height": None, "x_offset": 0, "y_offset": 0}


def test_clamp_region_absurd_size_is_clamped_to_advertised_caps():
    region = {"width": 200_000, "height": 200_000, "x_offset": 0, "y_offset": 0}
    clamped = escl._clamp_scan_region(region)
    assert clamped["width"] == escl.ESCL_MAX_WIDTH
    assert clamped["height"] == escl.ESCL_MAX_HEIGHT


def test_clamp_region_offset_plus_size_clamped_within_bounds():
    region = {"width": 2000, "height": 3000, "x_offset": 2000, "y_offset": 3000}
    clamped = escl._clamp_scan_region(region)
    assert clamped["x_offset"] + clamped["width"] <= escl.ESCL_MAX_WIDTH
    assert clamped["y_offset"] + clamped["height"] <= escl.ESCL_MAX_HEIGHT


async def test_create_scan_job_clamps_absurd_region_and_snaps_resolution(
    db, client, _captured_tasks
):
    await _enable_escl(db)
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03"
                    xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">
  <scan:XResolution>290</scan:XResolution>
  <pwg:DocumentFormat>application/pdf</pwg:DocumentFormat>
  <scan:ScanRegion>
    <pwg:XOffset>0</pwg:XOffset>
    <pwg:YOffset>0</pwg:YOffset>
    <pwg:Width>200000</pwg:Width>
    <pwg:Height>200000</pwg:Height>
  </scan:ScanRegion>
</scan:ScanSettings>
"""
    resp = await client.post("/eSCL/ScanJobs", content=xml)
    assert resp.status_code == 201
    job_id = resp.headers["location"].rsplit("/", 1)[-1]

    job = escl._scan_jobs[job_id]
    assert job["resolution"] == 300  # snapped from 290
    assert job["scan_region"]["width"] == escl.ESCL_MAX_WIDTH
    assert job["scan_region"]["height"] == escl.ESCL_MAX_HEIGHT

    # Let the background task finish (it'll fail -- no scanner configured --
    # but that's irrelevant to what this test checks).
    await asyncio.gather(*_captured_tasks, return_exceptions=True)


async def test_create_scan_job_width_only_falls_back_to_full_bed(db, client, _captured_tasks):
    await _enable_escl(db)
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03"
                    xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">
  <pwg:DocumentFormat>application/pdf</pwg:DocumentFormat>
  <scan:ScanRegion>
    <pwg:XOffset>0</pwg:XOffset>
    <pwg:YOffset>0</pwg:YOffset>
    <pwg:Width>1000</pwg:Width>
  </scan:ScanRegion>
</scan:ScanSettings>
"""
    resp = await client.post("/eSCL/ScanJobs", content=xml)
    assert resp.status_code == 201
    job_id = resp.headers["location"].rsplit("/", 1)[-1]

    job = escl._scan_jobs[job_id]
    assert job["scan_region"] == {"width": None, "height": None, "x_offset": 0, "y_offset": 0}

    await asyncio.gather(*_captured_tasks, return_exceptions=True)


# --------------------------------------------------------------------------- #
# F110: DELETE actually cancels an in-flight eSCL scan
# --------------------------------------------------------------------------- #
async def test_delete_cancels_in_flight_scan_task(db, client, monkeypatch, _captured_tasks):
    await _enable_escl(db)

    started = asyncio.Event()
    cancelled = asyncio.Event()

    class _HangingScanService:
        def is_busy(self) -> bool:
            return False

        async def scan(self, **kwargs):
            started.set()
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                cancelled.set()
                raise
            raise AssertionError("should have been cancelled")  # pragma: no cover

    monkeypatch.setattr(escl, "scan_service", _HangingScanService())
    monkeypatch.setattr(escl, "get_default_scanner_device", _fake_get_default_scanner_device)

    resp = await client.post("/eSCL/ScanJobs", content=_MINIMAL_SCAN_SETTINGS_XML)
    assert resp.status_code == 201
    job_id = resp.headers["location"].rsplit("/", 1)[-1]

    await asyncio.wait_for(started.wait(), timeout=2)
    assert escl._scan_jobs[job_id]["task"] is not None

    del_resp = await client.delete(f"/eSCL/ScanJobs/{job_id}")
    assert del_resp.status_code == 200
    assert cancelled.is_set()
    assert job_id not in escl._scan_jobs

    # Let the (now-cancelled) background task actually finish unwinding so
    # nothing leaks past the end of the test.
    await asyncio.gather(*_captured_tasks, return_exceptions=True)
