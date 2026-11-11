import asyncio

import pytest
from sqlalchemy import select

from app.exceptions import ExternalServiceError, ScanSourceError
from app.models import ScanJob
from app.routers import escl
from app.routers import scanners as scanners_router
from app.routers import system as system_router
from app.services import brother_protocol as bp
from app.services import scan_service as ss
from app.services.brother_client import BrotherScanClient
from app.services.net_guard import UnsafeHostError, assert_safe_host
from tests.brother_helpers import OK, FakeScanner
from tests.test_scanners_native_api import DEVICE, LEASE, _add, _rec, native  # noqa: F401


@pytest.fixture
def drain_fails(monkeypatch):
    async def boom(self):
        raise ConnectionResetError

    monkeypatch.setattr(asyncio.StreamWriter, "drain", boom)


async def test_send_failure_in_probe_is_external_service_error(drain_fails):
    async with FakeScanner([[OK]]) as srv:
        with pytest.raises(ExternalServiceError) as ei:
            await BrotherScanClient("127.0.0.1", srv.port).probe()
    assert "lost" in ei.value.detail


async def test_send_failure_in_scan_is_external_service_error(drain_fails, tmp_path):
    async with FakeScanner([[OK]]) as srv:
        with pytest.raises(ExternalServiceError):
            await BrotherScanClient("127.0.0.1", srv.port, retry_delay=0).scan(
                mode="TEXT", dpi=300, source="flatbed", out_dir=str(tmp_path)
            )


@pytest.mark.parametrize("host", ["a..b", "a" * 64 + ".example"])
async def test_malformed_hostnames_are_unsafe_not_unicode_errors(host):
    with pytest.raises(UnsafeHostError):
        await assert_safe_host(host)


async def test_host_with_empty_label_gives_422_from_scan(tmp_path):
    from app.exceptions import ScanRequestError
    from app.services.scan_service import ScanService

    with pytest.raises(ScanRequestError):
        await ScanService().scan(device="brothernet:a..b", scan_dir=str(tmp_path))


async def test_discover_keeps_other_results_when_one_host_fails(mdns_two, monkeypatch):
    async def check(device):
        if device.endswith(".2"):
            raise RuntimeError("boom")
        return {"available": True, "busy": False, "device": device, "output": ""}

    monkeypatch.setattr(scanners_router.scan_service, "check_device", check)
    result = await scanners_router._discover_native()
    assert [d["ip"] for d in result] == ["10.0.0.1"]


@pytest.fixture
def mdns_two(monkeypatch):
    async def fake():
        return [_rec("10.0.0.1"), _rec("10.0.0.2")]

    monkeypatch.setattr(scanners_router, "discover_scanner_services", fake)


async def test_hardware_model_string_is_addable(monkeypatch):
    async def fake():
        return [{"name": "Brother DCP-L2540DW series [30055c62cc21]", "ip": "10.0.0.7",
                 "port": bp.PORT, "model": "DCP-L2540DW series", "flatbed": True, "feeder": True}]

    async def check(device):
        return {"available": True, "busy": False, "device": device, "output": ""}

    monkeypatch.setattr(scanners_router, "discover_scanner_services", fake)
    monkeypatch.setattr(scanners_router.scan_service, "check_device", check)
    (entry,) = await scanners_router._discover_native()
    assert entry["supported"] is True and entry["device"] == "brothernet:10.0.0.7"


async def test_diagnostics_survives_native_query_failure(monkeypatch):
    class BadDB:
        async def execute(self, _s):
            raise RuntimeError("db down")

    async def fake_exec(*a, **k):
        raise FileNotFoundError

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    result = await scanners_router.scanner_diagnostics(db=BadDB(), _user=None)
    assert result["native"] == []


async def test_health_skips_probe_while_scanning(monkeypatch):
    system_router._reset_health_cache()

    async def device():
        return DEVICE

    async def never(_d):
        raise AssertionError("probed while busy")

    monkeypatch.setattr(system_router, "_default_native_device", device)
    monkeypatch.setattr(system_router.scan_service, "is_busy", lambda: True)
    monkeypatch.setattr(system_router.scan_service, "check_device", never)
    _, ok = await system_router._probe_subsystems()
    system_router._reset_health_cache()
    assert ok is True


async def test_health_native_probe_is_bounded(monkeypatch):
    system_router._reset_health_cache()
    real_wait_for = asyncio.wait_for
    seen = []

    async def device():
        return DEVICE

    async def slow(_d):
        await asyncio.sleep(30)

    async def spy(coro, timeout):
        seen.append(timeout)
        return await real_wait_for(coro, 0.05)

    monkeypatch.setattr(system_router, "_default_native_device", device)
    monkeypatch.setattr(system_router.scan_service, "check_device", slow)
    monkeypatch.setattr(system_router.asyncio, "wait_for", spy)
    _, ok = await system_router._probe_subsystems()
    system_router._reset_health_cache()
    assert ok is False and 5 in seen


def test_snap_to_native_resolutions():
    native_dpis = (100, 150, 200, 300, 400, 600)
    assert escl._snap_resolution(75, native_dpis) == 100
    assert escl._snap_resolution(450, native_dpis) in (400, 600)


async def test_capabilities_advertise_native_resolutions(db, client, monkeypatch):
    await _add(db, device="brothernet:10.0.0.5")
    from app.models import AppConfig

    db.add(AppConfig(key="escl_enabled", value="true"))
    await db.commit()
    text = (await client.get("/eSCL/ScannerCapabilities")).text
    assert "<scan:XResolution>400</scan:XResolution>" in text
    assert "<scan:XResolution>75</scan:XResolution>" not in text


async def test_sane_options_for_brother4_omit_75():
    from app.services.scan_service import ScanService

    assert 75 not in (await ScanService().get_options("brother4:net1;dev0"))["resolutions"]
    assert 75 in (await ScanService().get_options("airscan:e:x"))["resolutions"]


async def test_failed_scan_row_holds_detail_without_class_name(db, admin_client, monkeypatch):
    await _add(db)

    async def refuse(**kw):
        raise ScanSourceError("The document feeder is empty.")

    monkeypatch.setattr(ss.scan_service, "scan", refuse)
    resp = await admin_client.post("/api/scanner/scan", json={"resolution": 300})
    assert resp.status_code == 409
    row = (await db.execute(select(ScanJob))).scalars().first()
    await db.refresh(row)
    assert row.error_message == "The document feeder is empty."


async def test_scanimage_stderr_not_in_error_detail(tmp_path, monkeypatch, caplog):
    from tests.test_scan_service import _FakeProcess

    class Lines:
        def __init__(self):
            self.it = iter([b"scanimage: secret internal path /dev/x\n"])

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self.it)
            except StopIteration:
                raise StopAsyncIteration from None

    async def fake_exec(*a, **k):
        return _FakeProcess(returncode=1, stderr=Lines())

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with caplog.at_level("WARNING"), pytest.raises(ss.ScanError) as ei:
        await ss.ScanService().scan(device="test:dev", scan_dir=str(tmp_path))
    assert "secret" not in ei.value.detail and "secret" in caplog.text


async def test_escl_cancel_marks_db_row_failed(db, monkeypatch):
    started = asyncio.Event()

    class Hang:
        async def scan(self, **kw):
            started.set()
            await asyncio.sleep(3600)

    async def dev(_db):
        return "test:device"

    monkeypatch.setattr(escl, "scan_service", Hang())
    monkeypatch.setattr(escl, "get_default_scanner_device", dev)
    escl._scan_jobs["c1"] = {
        "state": "Pending", "resolution": 300, "color_mode": "Color", "format": "pdf",
        "source": "Flatbed", "scan_region": {}, "filepath": None, "served": False,
        "error": None, "terminal_at": None,
    }
    task = asyncio.create_task(escl._run_scan("c1"))
    await asyncio.wait_for(started.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    escl._scan_jobs.clear()
    row = (await db.execute(select(ScanJob))).scalars().first()
    await db.refresh(row)
    assert row.status == "failed" and row.error_message == "Scan cancelled"


# --- native feeder: whole stack in one PDF (DB tests below need the harness) ---
async def test_copy_native_feeder_prints_one_pdf(monkeypatch):
    from app.services import copy_service as cs
    from tests.test_copy_service import _FakeCups

    calls = []

    class Fake:
        async def scan(self, **kw):
            calls.append(kw)
            kw["page_count_callback"](4)
            return "sid", "/tmp/x.pdf"

    monkeypatch.setattr(cs, "scan_service", Fake())
    cups = _FakeCups()
    result = await cs.CopyService().copy(cups=cups, device=DEVICE, source="ADF")
    assert calls[0]["fmt"] == "pdf"
    assert result["format"] == "pdf" and result["page_count"] == 4
    assert cups.created[0][0] == "/tmp/x.pdf"


async def test_scan_endpoint_records_page_count_for_native_feeder(db, admin_client, monkeypatch):
    await _add(db)

    async def fake_scan(**kw):
        kw["page_count_callback"](3)
        return "sid", str(__file__)

    monkeypatch.setattr(ss.scan_service, "scan", fake_scan)
    resp = await admin_client.post("/api/scanner/scan", json={"source": "ADF", "format": "pdf"})
    assert resp.status_code == 201 and resp.json()["page_count"] == 3


async def test_scan_endpoint_native_feeder_jpeg_is_422_without_connecting(
    db, admin_client, native  # noqa: F811
):
    await _add(db)
    async with FakeScanner([]) as srv:
        native(srv)
        resp = await admin_client.post(
            "/api/scanner/scan", json={"source": "ADF", "format": "jpeg"}
        )
    assert resp.status_code == 422 and "PDF" in resp.json()["detail"] and srv.accepted == 0


async def _run_escl_job(db, fmt, source="ADF"):
    from app.models import AppConfig

    db.add(AppConfig(key="escl_enabled", value="true"))
    await db.commit()
    escl._scan_jobs["n1"] = {
        "state": "Pending", "resolution": 300, "color_mode": "Color", "format": fmt,
        "source": source, "scan_region": {}, "filepath": None, "served": False,
        "error": None, "terminal_at": None,
    }
    await escl._run_scan("n1")
    job = escl._scan_jobs.pop("n1")
    row = (await db.execute(select(ScanJob))).scalars().first()
    await db.refresh(row)
    return job, row


async def test_escl_native_feeder_pdf_job_sets_page_count(db, monkeypatch, tmp_path):
    await _add(db)

    async def fake_scan(**kw):
        kw["page_count_callback"](5)
        p = tmp_path / "s.pdf"
        p.write_bytes(b"%PDF")
        return "sid", str(p)

    monkeypatch.setattr(escl.scan_service, "scan", fake_scan)
    job, row = await _run_escl_job(db, "pdf")
    assert job["state"] == "Completed" and row.page_count == 5


async def test_unexpected_scan_error_is_stored_as_fixed_text(db, admin_client, monkeypatch):
    await _add(db, device="airscan:e:x")

    async def explode(**kw):
        raise RuntimeError("secret /dev/path")

    monkeypatch.setattr(ss.scan_service, "scan", explode)
    with pytest.raises(RuntimeError):
        await admin_client.post("/api/scanner/scan", json={})
    row = (await db.execute(select(ScanJob))).scalars().first()
    await db.refresh(row)
    assert row.error_message == "Scan failed"


async def test_copy_print_failure_keeps_the_scan_in_history(db, admin_client, monkeypatch):
    await _add(db)

    async def fake_scan(**kw):
        return "sid-1", "/tmp/scan.tiff"

    class Cups:
        def __init__(self, **kw):
            pass

        async def create_held_job(self, **kw):
            raise RuntimeError("cups down")

    async def queue(_db):
        return "q"

    from app.routers import copy as copy_router
    from app.services import copy_service as cs

    monkeypatch.setattr(cs.scan_service, "scan", fake_scan)
    monkeypatch.setattr(copy_router, "CupsService", Cups)
    monkeypatch.setattr(copy_router, "get_default_release_queue_name", queue)
    resp = await admin_client.post("/api/copy", json={})
    assert resp.status_code == 502 and "scan history" in resp.json()["detail"]
    row = (await db.execute(select(ScanJob))).scalars().first()
    assert row is not None and row.status == "completed"
