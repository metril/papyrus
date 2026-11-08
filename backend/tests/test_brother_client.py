import io
import logging
import os

import pytest
from PIL import Image

from app.exceptions import (
    ExternalServiceError,
    ScannerBusyError,
    ScanRequestError,
    ScanSourceError,
)
from app.services import brother_protocol as bp
from app.services.brother_client import BrotherScanClient
from tests.brother_helpers import (
    CLOSE,
    HOLD,
    MANIFEST,
    NAMES,
    NG,
    OK,
    FakeScanner,
    frame,
    lease_reply,
    load,
    page_end,
    sha,
    split_stream,
    with_page,
)


def client(port, **kw):
    kw.setdefault("retry_delay", 0)
    kw.setdefault("idle_timeout", 5)
    return BrotherScanClient("127.0.0.1", port, **kw)


def fixture_args(name):
    cfg = MANIFEST[name]
    return dict(mode=cfg["mode"], dpi=cfg["dpi"], source="flatbed", area_mm=tuple(cfg["area_mm"]))


def listing(out_dir):
    return sorted(os.listdir(out_dir))


@pytest.mark.parametrize("chunk", [None, 7])
@pytest.mark.parametrize("name", NAMES)
async def test_fixture_replay_pixel_exact(name, chunk, tmp_path):
    cfg = MANIFEST[name]
    async with FakeScanner([[(d, b) for d, b in load(name)]], chunk=chunk) as srv:
        pages = await client(srv.port).scan(out_dir=str(tmp_path), **fixture_args(name))
    assert srv.errors == [] and srv.trailing == [b""]
    assert len(pages) == 1
    with Image.open(pages[0].path) as img:
        assert list(img.size) == cfg["size"] and img.mode == cfg["pil_mode"]
        assert img.info["dpi"][0] == pytest.approx(cfg["dpi"], abs=1)
        assert sha(img.tobytes()) == cfg["sha256"]
    assert listing(tmp_path) == ["page-001.tiff"]  # spool removed


async def test_progress_row_mode(tmp_path):
    seen = []

    async def cb(page, pct):
        seen.append((page, pct))

    name = "20-fixture-mode-01"
    async with FakeScanner([load(name)]) as srv:
        await client(srv.port).scan(out_dir=str(tmp_path), progress=cb, **fixture_args(name))
    assert seen[0] == (1, 0) and seen[-1] == (1, 100)
    body = [p for _, p in seen[:-1]]
    assert body == sorted(body) and max(body) == 99


async def test_progress_jpeg_reports_per_page(tmp_path):
    seen = []

    async def cb(page, pct):
        seen.append((page, pct))

    name = "20-fixture-mode-04"
    async with FakeScanner([load(name)]) as srv:
        await client(srv.port).scan(out_dir=str(tmp_path), progress=cb, **fixture_args(name))
    assert seen == [(1, 0), (1, 100)]


def head(name, n=3):
    """Fixture segments up to and including the ESC X request."""
    return load(name)[:n + 1]


def row_script(name, frames_bytes):
    return head(name, 5)[:6] + [("S>C", frames_bytes)]


async def test_multi_page(tmp_path):
    name = "20-fixture-mode-01"
    frames, _ = split_stream(load(name)[-1][1])
    stream = b"".join(with_page(f, 1) for f in frames) + page_end(1)
    stream += b"".join(with_page(f, 2) for f in frames) + page_end(2) + b"\x80"
    script = load(name)[:-1] + [("S>C", stream)]
    async with FakeScanner([script]) as srv:
        pages = await client(srv.port).scan(out_dir=str(tmp_path), **fixture_args(name))
    assert srv.errors == []
    assert [os.path.basename(p.path) for p in pages] == ["page-001.tiff", "page-002.tiff"]
    for p in pages:
        with Image.open(p.path) as img:
            assert sha(img.tobytes()) == MANIFEST[name]["sha256"]


async def test_short_flatbed_page_is_padded_white(tmp_path):
    name = "20-fixture-mode-01"
    frames, _ = split_stream(load(name)[-1][1])
    stream = b"".join(frames[:-3]) + page_end(1) + b"\x80"
    async with FakeScanner([load(name)[:-1] + [("S>C", stream)]]) as srv:
        pages = await client(srv.port).scan(out_dir=str(tmp_path), **fixture_args(name))
    assert pages[0].height == 565
    with Image.open(pages[0].path) as img:
        full = Image.frombytes("1", (560, 565), b"\xff" * 70 * 565).tobytes()
        assert img.tobytes()[-3 * 70 :] == full[-3 * 70 :]
        assert img.getpixel((0, 564)) == 255


async def test_busy_401_then_ok(tmp_path):
    name = "20-fixture-mode-01"
    async with FakeScanner([[NG], load(name)]) as srv:
        pages = await client(srv.port).scan(out_dir=str(tmp_path), **fixture_args(name))
    assert srv.accepted == 2 and len(pages) == 1 and srv.errors == []


async def test_empty_lease_then_ok(tmp_path):
    name = "20-fixture-mode-01"
    segs = load(name)
    empty = [OK, segs[1], CLOSE]  # accept the lease request, then hang up
    async with FakeScanner([empty, segs]) as srv:
        pages = await client(srv.port).scan(out_dir=str(tmp_path), **fixture_args(name))
    assert srv.accepted == 2 and len(pages) == 1 and srv.errors == []


async def test_not_ready_lease_prefix_then_ok(tmp_path):
    name = "20-fixture-mode-01"
    segs = load(name)
    not_ready = [OK, segs[1], ("S>C", b"\x83\x00\x1d"), CLOSE]
    async with FakeScanner([not_ready, segs]) as srv:
        pages = await client(srv.port).scan(out_dir=str(tmp_path), **fixture_args(name))
    assert srv.accepted == 2 and len(pages) == 1 and srv.errors == []


async def test_busy_exhausted(tmp_path):
    async with FakeScanner([[NG], [NG], [NG]]) as srv:
        with pytest.raises(ScannerBusyError):
            await client(srv.port, busy_retries=3).scan(
                out_dir=str(tmp_path), **fixture_args("20-fixture-mode-01")
            )
    assert srv.accepted == 3


async def test_idle_timeout(tmp_path):
    name = "20-fixture-mode-01"
    script = load(name)[:-1] + [HOLD]
    async with FakeScanner([script]) as srv:
        with pytest.raises(ExternalServiceError) as ei:
            await client(srv.port, idle_timeout=0.3).scan(
                out_dir=str(tmp_path), **fixture_args(name)
            )
    assert "stopped responding" in ei.value.detail
    assert listing(tmp_path) == []


def refusal_script(name, adf, feeder_byte):
    segs = load(name)
    lease = ("S>C", lease_reply(adf=adf, **({"h_mm": 0, "h_px": 0} if adf == 1 else {})))
    return [segs[0], segs[1], lease, segs[3], ("S>C", bytes([feeder_byte]))]


@pytest.mark.parametrize(
    "adf,feeder_byte", [(1, bp.FEEDER_LOADED), (1, bp.FEEDER_EMPTY), (2, bp.FEEDER_LOADED)]
)
async def test_flatbed_refused_when_feeder_has_paper(adf, feeder_byte, tmp_path):
    name = "20-fixture-mode-01"
    async with FakeScanner([refusal_script(name, adf, feeder_byte)]) as srv:
        with pytest.raises(ScanSourceError) as ei:
            await client(srv.port).scan(out_dir=str(tmp_path), **fixture_args(name))
    assert "feeder" in ei.value.detail
    assert srv.errors == [] and srv.trailing == [b""]  # ESC X never sent


@pytest.mark.parametrize(
    "adf,feeder_byte", [(2, bp.FEEDER_EMPTY), (1, bp.FEEDER_EMPTY), (2, bp.FEEDER_LOADED)]
)
async def test_feeder_refused_when_empty(adf, feeder_byte, tmp_path):
    name = "20-fixture-mode-04"
    async with FakeScanner([refusal_script(name, adf, feeder_byte)]) as srv:
        with pytest.raises(ScanSourceError) as ei:
            await client(srv.port).scan(mode="CGRAY", dpi=300, source="adf", out_dir=str(tmp_path))
    assert "empty" in ei.value.detail
    assert srv.errors == [] and srv.trailing == [b""]


@pytest.mark.parametrize(
    "kw",
    [
        dict(mode="TEXT", dpi=250, source="flatbed"),
        dict(mode="TEXT", dpi=1200, source="flatbed"),
        dict(mode="BOGUS", dpi=300, source="flatbed"),
        dict(mode="TEXT", dpi=300, source="tray"),
        dict(mode="CGRAY", dpi=300, source="adf", area_mm=(0, 0, 50, 50)),
    ],
)
async def test_bad_request_rejected_before_connecting(kw, tmp_path):
    async with FakeScanner([]) as srv:
        with pytest.raises(ScanRequestError):
            await client(srv.port).scan(out_dir=str(tmp_path), **kw)
    assert srv.accepted == 0


def jpeg_bytes(width, real_rows, total_rows):
    import numpy as np

    arr = np.full((total_rows, width, 3), 128, np.uint8)
    arr[:real_rows, :, 0] = (np.arange(real_rows)[:, None] * 7 + 10) % 200 + 20
    arr[:real_rows, :, 1] = 60
    arr[:real_rows, :, 2] = 200
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, "JPEG", quality=95, subsampling=0)
    return buf.getvalue()


def jpeg_frames(data, page=1, size=20000):
    chunks = (data[i : i + size] for i in range(0, len(data), size))
    return b"".join(frame(bp.KIND_JPEG, page, c) for c in chunks)


async def test_feeder_jpeg_trims_decoder_fill(tmp_path):
    area = (0, 0, 64, 4175)
    lease = lease_reply(adf=1, w_px=64, h_mm=0, h_px=0)
    data = jpeg_bytes(64, 3216, 4175)
    script = [
        OK,
        ("C>S", bp.encode_lease_request(300, "CGRAY")),
        ("S>C", lease),
        ("C>S", bp.CMD_FEEDER_QUERY),
        ("S>C", bytes([bp.FEEDER_LOADED])),
        ("C>S", bp.encode_start(300, 300, "CGRAY", area)),
        ("S>C", jpeg_frames(data) + page_end(1) + b"\x80"),
    ]
    async with FakeScanner([script]) as srv:
        pages = await client(srv.port).scan(
            mode="CGRAY", dpi=300, source="adf", out_dir=str(tmp_path)
        )
    assert srv.errors == [] and srv.trailing == [b""]
    assert (pages[0].width, pages[0].height) == (64, 3216)
    with Image.open(pages[0].path) as img:
        assert img.size == (64, 3216)


async def test_feeder_jpeg_with_sof_taller_than_data(tmp_path):
    import struct

    data = bytearray(jpeg_bytes(64, 48, 48))
    i = data.find(b"\xff\xc0")
    struct.pack_into(">H", data, i + 5, 96)  # SOF now declares 96 rows
    lease = lease_reply(adf=1, w_px=64, h_mm=0, h_px=0)
    script = [
        OK,
        ("C>S", bp.encode_lease_request(300, "CGRAY")),
        ("S>C", lease),
        ("C>S", bp.CMD_FEEDER_QUERY),
        ("S>C", bytes([bp.FEEDER_LOADED])),
        ("C>S", bp.encode_start(300, 300, "CGRAY", (0, 0, 64, 4175))),
        ("S>C", jpeg_frames(bytes(data)) + page_end(1) + b"\x80"),
    ]
    async with FakeScanner([script]) as srv:
        pages = await client(srv.port).scan(
            mode="CGRAY", dpi=300, source="adf", out_dir=str(tmp_path)
        )
    assert 48 <= pages[0].height <= 56  # decoder junk is at most one MCU row


@pytest.mark.parametrize("code", [0xC3, 0xC5])
async def test_device_error_sends_reset(code, tmp_path, caplog):
    name = "20-fixture-mode-01"
    frames, _ = split_stream(load(name)[-1][1])
    script = load(name)[:-1] + [
        ("S>C", b"".join(frames[:10]) + bytes([code])),
        ("C>S", bp.CMD_RESET),
    ]
    async with FakeScanner([script]) as srv:
        with caplog.at_level(logging.WARNING), pytest.raises(ExternalServiceError) as ei:
            await client(srv.port).scan(out_dir=str(tmp_path), **fixture_args(name))
    assert "jam" in ei.value.detail and f"{code:02x}" not in ei.value.detail
    assert srv.errors == [] and f"0x{code:02x}" in caplog.text
    assert listing(tmp_path) == []


async def test_unknown_byte_protocol_error(tmp_path, caplog):
    name = "20-fixture-mode-01"
    script = load(name)[:-1] + [("S>C", b"\x99")]
    async with FakeScanner([script]) as srv:
        with caplog.at_level(logging.WARNING), pytest.raises(bp.BrotherProtocolError) as ei:
            await client(srv.port).scan(out_dir=str(tmp_path), **fixture_args(name))
    assert "0x99" in caplog.text and "99" not in ei.value.detail


async def test_malformed_packbits_and_wrong_row_length(tmp_path):
    name = "20-fixture-mode-01"
    for payload in (b"\x05ab", b"\xbb\x00\xbb\x00"):
        script = load(name)[:-1] + [("S>C", frame(bp.KIND_PACKBITS_ROW, 1, payload))]
        async with FakeScanner([script]) as srv:
            with pytest.raises(bp.BrotherProtocolError):
                await client(srv.port).scan(out_dir=str(tmp_path), **fixture_args(name))
        assert listing(tmp_path) == []


async def test_too_many_rows_rejected(tmp_path):
    name = "20-fixture-mode-01"
    row = frame(bp.KIND_PACKBITS_ROW, 1, b"\xbb\x00")
    script = load(name)[:-1] + [("S>C", row * 700)]
    async with FakeScanner([script]) as srv:
        with pytest.raises(bp.BrotherProtocolError):
            await client(srv.port).scan(out_dir=str(tmp_path), **fixture_args(name))
    assert listing(tmp_path) == []


async def test_connection_closed_mid_stream(tmp_path):
    name = "20-fixture-mode-01"
    frames, _ = split_stream(load(name)[-1][1])
    script = load(name)[:-1] + [("S>C", b"".join(frames[:20])), CLOSE]
    async with FakeScanner([script]) as srv:
        with pytest.raises(ExternalServiceError):
            await client(srv.port).scan(out_dir=str(tmp_path), **fixture_args(name))
    assert listing(tmp_path) == []


async def test_connect_refused(tmp_path):
    async with FakeScanner([]) as srv:
        port = srv.port
    with pytest.raises(ExternalServiceError):
        await client(port).scan(out_dir=str(tmp_path), **fixture_args("20-fixture-mode-01"))


async def test_probe_takes_lease_without_scanning():
    script = [OK, ("C>S", bp.encode_lease_request(300, "TEXT")), ("S>C", lease_reply())]
    async with FakeScanner([script]) as srv:
        result = await client(srv.port).probe()
    assert result == {"available": True, "busy": False}
    assert srv.errors == [] and srv.trailing == [b""]


async def test_probe_busy_counts_as_reachable():
    async with FakeScanner([[NG]]) as srv:
        result = await client(srv.port).probe()
    assert result == {"available": True, "busy": True}


async def test_probe_not_ready_lease_prefix_is_busy():
    script = [OK, ("C>S", bp.encode_lease_request(300, "TEXT")), ("S>C", b"\x83\x00\x1d"), CLOSE]
    async with FakeScanner([script]) as srv:
        result = await client(srv.port).probe()
    assert result == {"available": True, "busy": True}


async def test_probe_empty_lease_is_busy():
    script = [OK, ("C>S", bp.encode_lease_request(300, "TEXT")), CLOSE]
    async with FakeScanner([script]) as srv:
        result = await client(srv.port).probe()
    assert result["busy"] is True


async def test_probe_unreachable_raises():
    async with FakeScanner([]) as srv:
        port = srv.port
    with pytest.raises(ExternalServiceError):
        await client(port).probe()


def feeder_session(lease_extra=()):
    return [
        OK,
        ("C>S", bp.encode_lease_request(300, "TEXT")),
        ("S>C", lease_reply()),
        ("C>S", bp.CMD_FEEDER_QUERY),
        *lease_extra,
    ]


async def test_silent_greeting_is_busy_and_retried(tmp_path):
    name = "20-fixture-mode-01"
    async with FakeScanner([[HOLD], load(name)]) as srv:
        pages = await client(srv.port, handshake_timeout=0.2).scan(
            out_dir=str(tmp_path), **fixture_args(name)
        )
    assert srv.accepted == 2 and len(pages) == 1 and srv.errors == []


async def test_silent_lease_reply_is_busy_and_retried(tmp_path):
    name = "20-fixture-mode-01"
    silent = [OK, ("C>S", bp.encode_lease_request(300, "TEXT")), HOLD]
    async with FakeScanner([silent, load(name)]) as srv:
        pages = await client(srv.port, handshake_timeout=0.2).scan(
            out_dir=str(tmp_path), **fixture_args(name)
        )
    assert srv.accepted == 2 and len(pages) == 1


async def test_silent_feeder_reply_reconnects_before_scanning(tmp_path):
    name = "20-fixture-mode-01"
    silent = feeder_session([HOLD])
    async with FakeScanner([silent, load(name)]) as srv:
        pages = await client(srv.port, handshake_timeout=0.2).scan(
            out_dir=str(tmp_path), **fixture_args(name)
        )
    assert srv.accepted == 2 and len(pages) == 1 and srv.errors == []


async def test_always_silent_exhausts_as_busy(tmp_path):
    async with FakeScanner([[HOLD]] * 3) as srv:
        with pytest.raises(ScannerBusyError):
            await client(srv.port, handshake_timeout=0.1, busy_retries=3).scan(
                out_dir=str(tmp_path), **fixture_args("20-fixture-mode-01")
            )
    assert srv.accepted == 3


def test_default_retries_span_at_least_ninety_seconds():
    c = BrotherScanClient("127.0.0.1")
    assert (c.busy_retries - 1) * c.retry_delay >= 90 and c.handshake_timeout == 10


async def test_probe_silent_scanner_is_busy_within_bound():
    async with FakeScanner([[HOLD]]) as srv:
        result = await client(srv.port, probe_timeout=0.2).probe()
    assert result == {"available": True, "busy": True}
    assert BrotherScanClient("h").probe_timeout < 5


async def test_mid_stream_silence_still_raises_after_handshake_retries(tmp_path):
    name = "20-fixture-mode-01"
    script = load(name)[:-1] + [HOLD]
    async with FakeScanner([script]) as srv:
        with pytest.raises(ExternalServiceError) as ei:
            await client(srv.port, idle_timeout=0.3, handshake_timeout=5).scan(
                out_dir=str(tmp_path), **fixture_args(name)
            )
    assert "stopped responding" in ei.value.detail and srv.accepted == 1
