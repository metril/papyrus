"""Native Brother backend wiring in ``scan_service`` against a fake scanner
on a port-0 localhost socket. ``assert_safe_host`` rejects loopback, so tests
that reach the fake patch the guard out; the guard itself is tested unpatched."""
import asyncio
import os
import sys

import pytest
from PIL import Image

from app.exceptions import ExternalServiceError, ScanRequestError, ScanSourceError
from app.routers import escl
from app.services import brother_protocol as bp
from app.services import scan_service as ss
from app.services.scan_service import (
    BrotherNetBackend,
    SaneBackend,
    ScanService,
    parse_native_device,
    scan_service,
)
from tests.brother_helpers import (
    HOLD,
    NG,
    OK,
    FakeScanner,
    frame,
    lease_reply,
    load,
    page_end,
    split_stream,
    with_page,
)
from tests.test_brother_client import jpeg_bytes, jpeg_frames
from tests.test_scan_service import _FakeProcess, _HangingAsyncIter

DEVICE = "brothernet:127.0.0.1"
FLATBED = dict(left_mm=0, top_mm=0, width_mm=50, height_mm=50)


@pytest.fixture
def native(monkeypatch):
    """Point the native backend at a fake scanner and disable the SSRF guard."""

    def install(srv):
        monkeypatch.setattr(
            ss, "_brothernet_backend",
            BrotherNetBackend(srv.port, retry_delay=0, idle_timeout=5),
        )

    async def allow(_host):
        return None

    monkeypatch.setattr(ss, "assert_safe_host", allow)
    return install


def _scripts(name, pages=1):
    segs = load(name)
    frames, _ = split_stream(segs[-1][1])
    stream = b""
    for n in range(1, pages + 1):
        stream += b"".join(with_page(f, n) for f in frames) + page_end(n)
    return segs[:-1] + [("S>C", stream + b"\x80")]


def test_backend_for_selects_native():
    svc = ScanService()
    assert isinstance(svc._backend_for("brothernet:10.0.0.5"), BrotherNetBackend)
    assert isinstance(svc._backend_for("brother4:net1;dev0"), SaneBackend)


@pytest.mark.parametrize(
    "device",
    ["brothernet:", "brothernet:host name", "brothernet:10.0.0.1:54921", "brothernet:a/b",
     "brothernet:-bad", "brothernet:;rm", "brothernet:" + "a" * 300],
)
async def test_malformed_native_device_rejected(device, tmp_path):
    with pytest.raises(ScanRequestError):
        parse_native_device(device)
    with pytest.raises(ScanRequestError):
        await ScanService().scan(device=device, scan_dir=str(tmp_path))
    with pytest.raises(ScanRequestError):
        await ScanService().check_device(device)


async def test_loopback_native_host_blocked_by_guard(tmp_path):
    with pytest.raises(ScanRequestError) as ei:
        await ScanService().scan(device=DEVICE, scan_dir=str(tmp_path))
    assert "not allowed" in ei.value.detail
    assert not ScanService().is_busy()


@pytest.mark.parametrize(
    "name,mode", [("20-fixture-mode-01", "Lineart"), ("20-fixture-mode-03", "Gray"),
                  ("20-fixture-mode-04", "Color")],
)
async def test_flatbed_scan_modes_and_progress(name, mode, tmp_path, native):
    seen = []

    async def cb(scan_id, pct):
        seen.append((scan_id, pct))

    async with FakeScanner([load(name)]) as srv:
        native(srv)
        scan_id, path = await ScanService().scan(
            resolution=300, mode=mode, fmt="tiff", source="Flatbed", device=DEVICE,
            scan_dir=str(tmp_path), progress_callback=cb, **FLATBED,
        )
    assert srv.errors == []
    assert path == str(tmp_path / f"{scan_id}.tiff")
    with Image.open(path) as img:
        assert img.size == (560, 565)
    assert seen and all(s == scan_id for s, _ in seen) and seen[-1][1] == 100.0
    assert os.listdir(tmp_path) == [f"{scan_id}.tiff"]


async def test_scan_converts_to_pdf(tmp_path, native):
    async with FakeScanner([load("20-fixture-mode-01")]) as srv:
        native(srv)
        _, path = await ScanService().scan(
            resolution=300, mode="Lineart", fmt="pdf", device=DEVICE,
            scan_dir=str(tmp_path), **FLATBED,
        )
    assert path.endswith(".pdf") and open(path, "rb").read(4) == b"%PDF"
    assert os.listdir(tmp_path) == [os.path.basename(path)]


async def test_adf_single_page_request_stops_after_first_page(tmp_path, native):
    data = jpeg_bytes(64, 100, 100)

    def jpg(n):
        return jpeg_frames(data, page=n)

    script = [
        OK,
        ("C>S", bp.encode_lease_request(300, "CGRAY")),
        ("S>C", lease_reply(adf=1, w_px=64, h_mm=0, h_px=0)),
        ("C>S", bp.CMD_FEEDER_QUERY),
        ("S>C", bytes([bp.FEEDER_LOADED])),
        ("C>S", bp.encode_start(300, 300, "CGRAY", (0, 0, 64, 4175))),
        ("S>C", jpg(1) + page_end(1) + jpg(2) + page_end(2) + b"\x80"),
    ]
    async with FakeScanner([script]) as srv:
        native(srv)
        scan_id, path = await ScanService().scan(
            resolution=300, mode="Color", fmt="tiff", source="ADF", device=DEVICE,
            scan_dir=str(tmp_path),
        )
    assert srv.errors == []
    assert os.listdir(tmp_path) == [f"{scan_id}.tiff"]
    with Image.open(path) as img:
        assert img.size[0] == 64


async def test_adf_ignores_scan_region(tmp_path, native):
    script = [
        OK,
        ("C>S", bp.encode_lease_request(300, "CGRAY")),
        ("S>C", lease_reply(adf=1, w_px=64, h_mm=0, h_px=0)),
        ("C>S", bp.CMD_FEEDER_QUERY),
        ("S>C", bytes([bp.FEEDER_LOADED])),
        ("C>S", bp.encode_start(300, 300, "CGRAY", (0, 0, 64, 4175))),
        ("S>C", jpeg_frames(jpeg_bytes(64, 50, 50)) + page_end(1) + b"\x80"),
    ]
    async with FakeScanner([script]) as srv:
        native(srv)
        await ScanService().scan(
            resolution=300, mode="Color", fmt="tiff", source="ADF", device=DEVICE,
            scan_dir=str(tmp_path), **FLATBED,
        )
    assert srv.errors == []


async def test_client_errors_reach_caller_unchanged(tmp_path, native):
    segs = load("20-fixture-mode-01")
    refusal = [segs[0], segs[1], ("S>C", lease_reply(adf=1, h_mm=0, h_px=0)), segs[3],
               ("S>C", bytes([bp.FEEDER_LOADED]))]
    async with FakeScanner([refusal]) as srv:
        native(srv)
        with pytest.raises(ScanSourceError):
            await ScanService().scan(
                resolution=300, mode="Lineart", source="Flatbed", device=DEVICE,
                scan_dir=str(tmp_path), **FLATBED,
            )
    with pytest.raises(ScanRequestError):
        await ScanService().scan(
            resolution=75, mode="Color", device=DEVICE, scan_dir=str(tmp_path)
        )
    assert os.listdir(tmp_path) == []


async def test_connect_failure_is_external_service_error(tmp_path, native):
    async with FakeScanner([]) as srv:
        native(srv)
    with pytest.raises(ExternalServiceError):
        await ScanService().scan(device=DEVICE, scan_dir=str(tmp_path))


@pytest.mark.parametrize(
    "mode,code", [("Lineart", "TEXT"), ("Gray", "GRAY64"), ("Color", "CGRAY")]
)
async def test_batch_merges_with_real_img2pdf(mode, code, tmp_path, monkeypatch, native):
    pytest.importorskip("img2pdf")
    real_exec = asyncio.create_subprocess_exec

    async def exec_(*args, **kw):
        if args[0] == "img2pdf":
            args = (sys.executable, "-m", "img2pdf", *args[1:])
        return await real_exec(*args, **kw)

    monkeypatch.setattr(ss.asyncio, "create_subprocess_exec", exec_)
    spec = bp.get_mode(code)

    def body(n):
        if spec.jpeg:
            return jpeg_frames(jpeg_bytes(64, 40, 40), page=n)
        return b"".join(
            frame(bp.KIND_RAW_ROW, n, bytes(spec.row_bytes(64))) for _ in range(40)
        )

    script = [
        OK,
        ("C>S", bp.encode_lease_request(300, code)),
        ("S>C", lease_reply(adf=1, w_px=64, h_mm=0, h_px=0)),
        ("C>S", bp.CMD_FEEDER_QUERY),
        ("S>C", bytes([bp.FEEDER_LOADED])),
        ("C>S", bp.encode_start(300, 300, code, (0, 0, 64, 4175))),
        ("S>C", body(1) + page_end(1) + body(2) + page_end(2) + b"\x80"),
    ]
    async with FakeScanner([script]) as srv:
        native(srv)
        scan_id, pdf, count = await ScanService().scan_batch(
            resolution=300, mode=mode, device=DEVICE, scan_dir=str(tmp_path)
        )
    assert srv.errors == [] and count == 2
    assert open(pdf, "rb").read(4) == b"%PDF"
    assert os.listdir(tmp_path) == [f"{scan_id}.pdf"]


async def test_probe_native(native):
    script = [OK, ("C>S", bp.encode_lease_request(300, "TEXT")), ("S>C", lease_reply())]
    async with FakeScanner([script]) as srv:
        native(srv)
        result = await ScanService().check_device(DEVICE)
    assert result["available"] is True and result["busy"] is False
    assert srv.errors == []


async def test_probe_native_busy_and_unreachable(native):
    async with FakeScanner([[NG]]) as srv:
        native(srv)
        busy = await ScanService().check_device(DEVICE)
    assert busy["available"] is True and busy["busy"] is True
    async with FakeScanner([]) as srv:
        native(srv)
    result = await ScanService().check_device(DEVICE)
    assert result["available"] is False


def test_native_options():
    opts = asyncio.run(ScanService().get_options("brothernet:10.0.0.5"))
    assert opts["resolutions"] == [100, 150, 200, 300, 400, 600]
    assert asyncio.run(ScanService().get_options("brother4:x"))["resolutions"][0] == 75


async def test_cancel_native_scan_drops_connection_and_frees_lock(tmp_path, native):
    name = "20-fixture-mode-01"
    script = load(name)[:-1] + [HOLD]
    svc = ScanService()
    async with FakeScanner([script]) as srv:
        native(srv)
        task = asyncio.create_task(svc.scan(
            resolution=300, mode="Lineart", fmt="pdf", device=DEVICE,
            scan_dir=str(tmp_path), **FLATBED,
        ))
        await asyncio.sleep(0.3)
        assert svc.is_busy()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.1)
    assert srv.errors == [] and srv.trailing == [b""]
    assert not svc.is_busy() and os.listdir(tmp_path) == []


@pytest.fixture
def escl_jobs():
    escl._scan_jobs.clear()
    yield escl._scan_jobs
    escl._scan_jobs.clear()


async def test_escl_cancel_native(tmp_path, native, escl_jobs):
    script = load("20-fixture-mode-01")[:-1] + [HOLD]
    async with FakeScanner([script]) as srv:
        native(srv)
        task = asyncio.create_task(scan_service.scan(
            resolution=300, mode="Lineart", fmt="pdf", device=DEVICE,
            scan_dir=str(tmp_path), **FLATBED,
        ))
        escl_jobs["j1"] = {"state": "Processing", "task": task, "filepath": None}
        await asyncio.sleep(0.3)
        resp = await escl.cancel_scan_job("j1")
        await asyncio.sleep(0.1)
    assert resp.status_code == 200 and task.cancelled()
    assert srv.errors == [] and srv.trailing == [b""]
    assert not scan_service.is_busy() and os.listdir(tmp_path) == []


async def test_escl_cancel_sane_kills_process(tmp_path, monkeypatch, escl_jobs):
    proc = _FakeProcess(stderr=_HangingAsyncIter())

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    task = asyncio.create_task(scan_service.scan(
        resolution=300, mode="Color", fmt="pdf", device="test:device", scan_dir=str(tmp_path)
    ))
    escl_jobs["j2"] = {"state": "Processing", "task": task, "filepath": None}
    await asyncio.sleep(0.05)
    resp = await escl.cancel_scan_job("j2")
    assert resp.status_code == 200 and task.cancelled()
    assert proc.killed and proc.waited and not scan_service.is_busy()
