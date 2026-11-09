"""eSCL feeder awareness for `brothernet:` devices: failed jobs end the client's
polling, impossible sources are refused before a job exists, and ScannerStatus /
ScannerCapabilities describe the feeder. The printer is a port-0 fake."""
import asyncio
import socket
import xml.etree.ElementTree as ET

import pytest
from sqlalchemy import select

from app.exceptions import ScanSourceError
from app.models import ScanJob
from app.routers import escl
from app.services import brother_protocol as bp
from app.services import scan_service as ss
from app.services.scan_service import BrotherNetBackend, scan_service
from tests.brother_helpers import NG, OK, FakeScanner, lease_reply
from tests.test_api_escl import (
    _MINIMAL_SCAN_SETTINGS_XML,
    _enable_escl,
    _patch_scan,
)

NS = {"scan": escl.ESCL_NS, "pwg": escl.PWG_NS}
NATIVE = "brothernet:127.0.0.1"
FEEDER_XML = _MINIMAL_SCAN_SETTINGS_XML.replace(b"Platen", b"Feeder")


@pytest.fixture
def tasks(monkeypatch):
    """Every task the router spawns, so tests can await the background scan."""
    spawned: list[asyncio.Task] = []
    real = asyncio.create_task

    def capture(coro, *args, **kwargs):
        task = real(coro, *args, **kwargs)
        spawned.append(task)
        return task

    monkeypatch.setattr(escl.asyncio, "create_task", capture)
    return spawned


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    escl._adf_cache.clear()
    monkeypatch.setattr(escl, "_adf_probing", False)

    async def allow(_host):
        return None

    monkeypatch.setattr(ss, "assert_safe_host", allow)
    yield
    escl._adf_cache.clear()


def read_script(lease_paper: bool, query_paper: bool):
    return [
        OK,
        ("C>S", bp.encode_lease_request(300, "TEXT")),
        ("S>C", lease_reply(adf=1 if lease_paper else 2)),
        ("C>S", bp.CMD_FEEDER_QUERY),
        ("S>C", bytes([bp.FEEDER_LOADED if query_paper else bp.FEEDER_EMPTY])),
    ]


def use_printer(monkeypatch, port):
    monkeypatch.setattr(
        ss, "_brothernet_backend", BrotherNetBackend(port, retry_delay=0, handshake_timeout=1)
    )

    async def device(_db):
        return NATIVE

    device.native = True
    monkeypatch.setattr(escl, "get_default_scanner_device", device)


def fake_service(monkeypatch, tmp_path, error=None):
    scan_file = tmp_path / "s.pdf"
    scan_file.write_bytes(b"%PDF-1.4\n")
    device = escl.get_default_scanner_device
    fake = _patch_scan(monkeypatch, str(scan_file), error=error)
    if getattr(device, "native", False):
        monkeypatch.setattr(escl, "get_default_scanner_device", device)
    fake.native_feeder_reading = scan_service.native_feeder_reading
    return fake


def closed_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def no_job_rows(db):
    await db.rollback()
    return (await db.execute(select(ScanJob))).scalars().all() == []


# -- failed jobs end polling ----------------------------------------------------


async def test_failed_job_is_410_not_503_and_status_shows_aborted(
    db, client, monkeypatch, tmp_path, tasks
):
    await _enable_escl(db)
    error = ScanSourceError("There is paper in the document feeder.")
    fake_service(monkeypatch, tmp_path, error=error)
    resp = await client.post("/eSCL/ScanJobs", content=_MINIMAL_SCAN_SETTINGS_XML)
    assert resp.status_code == 201
    job_id = resp.headers["location"].rsplit("/", 1)[-1]
    await asyncio.gather(*tasks)

    nxt = await client.get(f"/eSCL/ScanJobs/{job_id}/NextDocument")
    assert nxt.status_code == 410
    assert "paper in the document feeder" in nxt.text

    root = ET.fromstring((await client.get("/eSCL/ScannerStatus")).text)
    info = root.find("scan:Jobs/scan:JobInfo", NS)
    assert info.find("pwg:JobState", NS).text == "Aborted"
    assert info.find("pwg:JobStateReasons/pwg:JobStateReason", NS).text == "AbortedBySystem"

    assert (await client.delete(f"/eSCL/ScanJobs/{job_id}")).status_code == 200
    assert (await client.get(f"/eSCL/ScanJobs/{job_id}/NextDocument")).status_code == 404


async def test_finished_job_still_ends_with_404(client, monkeypatch, tmp_path):
    f = tmp_path / "s.pdf"
    f.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr(escl, "_purge_stale_jobs", lambda: None)
    escl._scan_jobs["j"] = {"state": "Completed", "served": False, "held": False,
                            "format": "pdf", "filepath": str(f)}
    try:
        assert (await client.get("/eSCL/ScanJobs/j/NextDocument")).status_code == 200
        assert (await client.get("/eSCL/ScanJobs/j/NextDocument")).status_code == 404
    finally:
        escl._scan_jobs.clear()


async def test_scan_in_progress_stays_503(client, monkeypatch):
    monkeypatch.setattr(escl, "_NEXT_DOCUMENT_WAIT_SECONDS", 0.01)
    monkeypatch.setattr(escl, "_purge_stale_jobs", lambda: None)
    task = asyncio.create_task(asyncio.sleep(30))
    escl._scan_jobs["j"] = {"state": "Processing", "task": task}
    try:
        assert (await client.get("/eSCL/ScanJobs/j/NextDocument")).status_code == 503
    finally:
        task.cancel()
        escl._scan_jobs.clear()


# -- refusal before a job exists --------------------------------------------------


@pytest.mark.parametrize("lease_paper,query_paper", [(True, True), (False, True), (True, False)])
async def test_platen_with_paper_in_feeder_is_409_and_nothing_starts(
    db, client, monkeypatch, tmp_path, tasks, lease_paper, query_paper
):
    await _enable_escl(db)
    async with FakeScanner([read_script(lease_paper, query_paper)]) as srv:
        use_printer(monkeypatch, srv.port)
        fake = fake_service(monkeypatch, tmp_path)
        resp = await client.post("/eSCL/ScanJobs", content=_MINIMAL_SCAN_SETTINGS_XML)
    assert resp.status_code == 409
    assert escl._scan_jobs == {} and fake.calls == []
    assert srv.errors == [] and srv.trailing == [b""] and srv.accepted == 1
    assert await no_job_rows(db)


@pytest.mark.parametrize("lease_paper,query_paper", [(False, False), (True, False), (False, True)])
async def test_feeder_not_loaded_is_409_and_nothing_starts(
    db, client, monkeypatch, tmp_path, tasks, lease_paper, query_paper
):
    await _enable_escl(db)
    async with FakeScanner([read_script(lease_paper, query_paper)]) as srv:
        use_printer(monkeypatch, srv.port)
        fake = fake_service(monkeypatch, tmp_path)
        resp = await client.post("/eSCL/ScanJobs", content=FEEDER_XML)
    assert resp.status_code == 409
    assert escl._scan_jobs == {} and fake.calls == []
    assert srv.errors == [] and srv.trailing == [b""]
    assert await no_job_rows(db)


async def test_matching_source_creates_the_job(db, client, monkeypatch, tmp_path, tasks):
    await _enable_escl(db)
    async with FakeScanner([read_script(False, False)]) as srv:
        use_printer(monkeypatch, srv.port)
        fake_service(monkeypatch, tmp_path)
        resp = await client.post("/eSCL/ScanJobs", content=_MINIMAL_SCAN_SETTINGS_XML)
        await asyncio.gather(*tasks)
    assert resp.status_code == 201


@pytest.mark.parametrize("busy", [True, False])
async def test_unreadable_printer_falls_through(
    db, client, monkeypatch, tmp_path, tasks, busy
):
    await _enable_escl(db)
    fake = fake_service(monkeypatch, tmp_path)
    if busy:
        async with FakeScanner([[NG]]) as srv:
            use_printer(monkeypatch, srv.port)
            resp = await client.post("/eSCL/ScanJobs", content=_MINIMAL_SCAN_SETTINGS_XML)
    else:
        use_printer(monkeypatch, closed_port())
        resp = await client.post("/eSCL/ScanJobs", content=_MINIMAL_SCAN_SETTINGS_XML)
    await asyncio.gather(*tasks)
    assert resp.status_code == 201
    assert len(fake.calls) == 1


async def test_other_backends_are_not_probed(db, client, monkeypatch, tmp_path, tasks):
    await _enable_escl(db)
    fake = fake_service(monkeypatch, tmp_path)
    called = []
    fake.native_feeder_reading = lambda *a: called.append(a)
    resp = await client.post("/eSCL/ScanJobs", content=FEEDER_XML)
    await asyncio.gather(*tasks)
    assert resp.status_code == 201 and called == []


# -- InputSource parsing ----------------------------------------------------------


def settings_xml(source_tag: str, value: str) -> bytes:
    return (
        '<scan:ScanSettings xmlns:scan="%s" xmlns:pwg="%s">'
        "<pwg:Version>2.6</pwg:Version><%s>%s</%s></scan:ScanSettings>"
        % (escl.ESCL_NS, escl.PWG_NS, source_tag, value, source_tag)
    ).encode()


@pytest.mark.parametrize(
    "tag,value,expected",
    [
        ("pwg:InputSource", "Feeder", "ADF"),
        ("pwg:InputSource", "Platen", "Flatbed"),
        ("scan:InputSource", "ADF", "ADF"),
        ("pwg:InputSource", "feeder", "ADF"),
        ("pwg:InputSource", "Camera", "Flatbed"),
    ],
)
async def test_input_source_values(
    db, client, monkeypatch, tmp_path, tasks, tag, value, expected
):
    await _enable_escl(db)
    fake = fake_service(monkeypatch, tmp_path)
    resp = await client.post("/eSCL/ScanJobs", content=settings_xml(tag, value))
    await asyncio.gather(*tasks)
    assert resp.status_code == 201
    assert fake.calls[0]["source"] == expected


async def test_input_source_default_namespace_and_missing(
    db, client, monkeypatch, tmp_path, tasks
):
    await _enable_escl(db)
    fake = fake_service(monkeypatch, tmp_path)
    body = f'<ScanSettings xmlns="{escl.ESCL_NS}"><InputSource>Feeder</InputSource></ScanSettings>'
    assert (await client.post("/eSCL/ScanJobs", content=body.encode())).status_code == 201
    await asyncio.gather(*tasks)
    escl._scan_jobs.clear()
    assert (await client.post("/eSCL/ScanJobs", content=b"<ScanSettings/>")).status_code == 201
    await asyncio.gather(*tasks)
    assert [c["source"] for c in fake.calls] == ["ADF", "Flatbed"]


# -- ScannerStatus AdfState -------------------------------------------------------


def adf_state(resp):
    el = ET.fromstring(resp.text).find("scan:AdfState", NS)
    return None if el is None else el.text


@pytest.mark.parametrize(
    "lease_paper,query_paper,expected",
    [(True, True, "ScannerAdfLoaded"), (False, False, "ScannerAdfEmpty")],
)
async def test_status_reports_adf_state(
    db, client, monkeypatch, lease_paper, query_paper, expected
):
    await _enable_escl(db)
    async with FakeScanner([read_script(lease_paper, query_paper)]) as srv:
        use_printer(monkeypatch, srv.port)
        resp = await client.get("/eSCL/ScannerStatus")
    assert resp.status_code == 200 and adf_state(resp) == expected
    assert srv.errors == [] and srv.trailing == [b""]


async def test_status_is_cached_then_stale_when_printer_goes_quiet(db, client, monkeypatch):
    await _enable_escl(db)
    async with FakeScanner([read_script(True, True)]) as srv:
        use_printer(monkeypatch, srv.port)
        assert adf_state(await client.get("/eSCL/ScannerStatus")) == "ScannerAdfLoaded"
        assert adf_state(await client.get("/eSCL/ScannerStatus")) == "ScannerAdfLoaded"
        assert srv.accepted == 1
        dev, (ts, val) = next(iter(escl._adf_cache.items()))
        escl._adf_cache[dev] = (ts - 30, val)  # expired but within the stale limit
        assert adf_state(await client.get("/eSCL/ScannerStatus")) == "ScannerAdfLoaded"
        assert srv.accepted == 2  # re-read attempted, printer was busy
        escl._adf_cache[dev] = (ts - 600, val)  # too old to serve
        assert adf_state(await client.get("/eSCL/ScannerStatus")) is None


async def test_status_omits_adf_state_when_unreadable(db, client, monkeypatch):
    await _enable_escl(db)
    use_printer(monkeypatch, closed_port())
    assert adf_state(await client.get("/eSCL/ScannerStatus")) is None


async def test_status_does_not_probe_during_a_scan(db, client, monkeypatch):
    await _enable_escl(db)
    async with FakeScanner([read_script(True, True)]) as srv:
        use_printer(monkeypatch, srv.port)
        async with scan_service._lock:
            assert adf_state(await client.get("/eSCL/ScannerStatus")) is None
        escl._scan_jobs["j"] = {"state": "Processing", "task": None}
        try:
            assert adf_state(await client.get("/eSCL/ScannerStatus")) is None
        finally:
            escl._scan_jobs.clear()
    assert srv.accepted == 0


async def test_status_is_bounded_when_the_printer_is_slow(db, client, monkeypatch):
    await _enable_escl(db)
    monkeypatch.setattr(escl, "_ADF_STATUS_TIMEOUT", 0.2)
    async with FakeScanner([[("HOLD", b"")]]) as srv:
        use_printer(monkeypatch, srv.port)
        loop = asyncio.get_running_loop()
        start = loop.time()
        resp = await client.get("/eSCL/ScannerStatus")
    assert resp.status_code == 200 and adf_state(resp) is None
    assert loop.time() - start < 2


async def test_status_has_no_adf_state_for_other_devices(db, client):
    await _enable_escl(db)
    assert adf_state(await client.get("/eSCL/ScannerStatus")) is None


# -- capabilities ---------------------------------------------------------------


async def caps(client, monkeypatch, device):
    async def get_device(_db):
        return device

    monkeypatch.setattr(escl, "get_default_scanner_device", get_device)
    resp = await client.get("/eSCL/ScannerCapabilities")
    assert resp.status_code == 200
    return ET.fromstring(resp.text)


async def test_native_capabilities_describe_the_feeder(db, client, monkeypatch):
    await _enable_escl(db)
    root = await caps(client, monkeypatch, NATIVE)
    assert root.find("scan:Platen/scan:PlatenInputCaps", NS) is not None
    adf = root.find("scan:Adf", NS)
    assert [c.tag.split("}")[1] for c in adf] == [
        "AdfSimplexInputCaps", "FeederCapacity", "AdfOptions"
    ]
    assert adf.find("scan:FeederCapacity", NS).text == "35"
    assert adf.find("scan:AdfOptions/scan:AdfOption", NS).text == "DetectPaperLoaded"
    assert adf.find("scan:AdfDuplexInputCaps", NS) is None


async def test_brscan4_capabilities_have_capacity_but_no_detection(db, client, monkeypatch):
    await _enable_escl(db)
    adf = (await caps(client, monkeypatch, "brother4:MFC0")).find("scan:Adf", NS)
    assert adf.find("scan:FeederCapacity", NS).text == "35"
    assert adf.find("scan:AdfOptions", NS) is None


async def test_airscan_capabilities_stay_as_before(db, client, monkeypatch):
    await _enable_escl(db)
    adf = (await caps(client, monkeypatch, "airscan:e0:X")).find("scan:Adf", NS)
    assert [c.tag.split("}")[1] for c in adf] == ["AdfSimplexInputCaps"]
