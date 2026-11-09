"""Paper in the feeder takes over a glass request, and an abandoned feeder
stream is read to its job end before the connection closes. Port-0 fake
scanner, no DB, xdist-safe."""
import asyncio
import os
import time

import pytest

from app.exceptions import ExternalServiceError
from app.services import brother_protocol as bp
from app.services.brother_client import BrotherScanClient
from tests.brother_helpers import CLOSE, OK, FakeScanner, gate, lease_reply, page_end
from tests.test_brother_client import jpeg_bytes, jpeg_frames

FEEDER_AREA = (0, 0, 64, 4175)


def client(port, **kw):
    kw.setdefault("retry_delay", 0)
    kw.setdefault("idle_timeout", 5)
    return BrotherScanClient("127.0.0.1", port, **kw)


def page(n):
    return jpeg_frames(jpeg_bytes(64, 40, 40), page=n) + page_end(n)


def session(lease_paper=True, query_paper=True, area=FEEDER_AREA, *body):
    return [
        OK,
        ("C>S", bp.encode_lease_request(300, "CGRAY")),
        ("S>C", lease_reply(adf=1 if lease_paper else 2, w_px=64, h_mm=0, h_px=0)),
        ("C>S", bp.CMD_FEEDER_QUERY),
        ("S>C", bytes([bp.FEEDER_LOADED if query_paper else bp.FEEDER_EMPTY])),
        ("C>S", bp.encode_start(300, 300, "CGRAY", area)),
        *body,
    ]


def feeder_scan(srv, **kw):
    return client(srv.port, **kw.pop("client", {})).scan(
        mode="CGRAY", dpi=300, source=kw.pop("source", "adf"), out_dir=kw.pop("out_dir"), **kw
    )


# -- a glass request with paper in the feeder is a full-page feeder request ---------


@pytest.mark.parametrize("lease_paper,query_paper", [(True, True), (False, True), (True, False)])
async def test_glass_request_with_paper_is_fed_in_full_and_keeps_every_sheet(
    lease_paper, query_paper, tmp_path
):
    script = session(lease_paper, query_paper, FEEDER_AREA,
                     ("S>C", page(1) + page(2) + page(3) + b"\x80"))
    async with FakeScanner([script]) as srv:  # the exact START bytes are verified
        pages = await feeder_scan(
            srv, source="flatbed", area_mm=(10, 10, 40, 40), out_dir=str(tmp_path)
        )
    assert srv.errors == [] and srv.trailing == [b""]
    assert len(pages) == 3 and all(p.from_feeder for p in pages)


async def test_glass_request_with_empty_feeder_is_unchanged_with_its_partial_area(tmp_path):
    area = bp.area_to_pixels(300, 10, 10, 20, 20, 640, 800)
    script = [
        OK,
        ("C>S", bp.encode_lease_request(300, "CGRAY")),
        ("S>C", lease_reply(adf=2, w_px=640, h_px=800)),
        ("C>S", bp.CMD_FEEDER_QUERY),
        ("S>C", bytes([bp.FEEDER_EMPTY])),
        ("C>S", bp.encode_start(300, 300, "CGRAY", area)),
        ("S>C", page(1) + b"\x80"),
    ]
    async with FakeScanner([script]) as srv:
        pages = await feeder_scan(
            srv, source="flatbed", area_mm=(10, 10, 20, 20), out_dir=str(tmp_path)
        )
    assert srv.errors == [] and len(pages) == 1 and not pages[0].from_feeder


# -- drain ----------------------------------------------------------------------------


def split_page(n):
    data = page(n)
    return data[: len(data) // 2], data[len(data) // 2 :]


async def abandon(task, srv, name, *, cancels=1):
    await srv.wait_reached(name)
    for _ in range(cancels):
        task.cancel()
        await asyncio.sleep(0.05)


@pytest.mark.parametrize("cut", ["before_data", "mid_frame"])
async def test_cancel_reads_the_feeder_stream_to_job_end_before_closing(cut, tmp_path):
    head, tail = split_page(2)
    first = b"" if cut == "before_data" else page(1) + head
    rest = (tail if first else page(2)) if cut == "mid_frame" else page(1) + page(2)
    segs = ([("S>C", first)] if first else []) + [gate("g"), ("S>C", rest + b"\x80")]
    async with FakeScanner([session(True, True, FEEDER_AREA, *segs)]) as srv:
        task = asyncio.create_task(feeder_scan(srv, out_dir=str(tmp_path)))
        try:
            await abandon(task, srv, "g")
            assert not task.done()  # still draining, connection kept
            started = time.monotonic()
            srv.open("g")
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
            assert time.monotonic() - started < 1.5
        finally:
            srv.open("g")
    assert srv.errors == [] and srv.trailing == [b""]
    assert os.listdir(tmp_path) == []


async def test_repeated_cancel_does_not_cut_the_drain_short(tmp_path):
    head, tail = split_page(1)
    segs = [("S>C", head), gate("g"), ("S>C", tail + b"\x80")]
    async with FakeScanner([session(True, True, FEEDER_AREA, *segs)]) as srv:
        task = asyncio.create_task(feeder_scan(srv, out_dir=str(tmp_path)))
        try:
            await abandon(task, srv, "g", cancels=3)
            assert not task.done()
            srv.open("g")
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
        finally:
            srv.open("g")
    assert srv.errors == [] and srv.trailing == [b""]


async def test_silent_device_ends_the_drain_at_the_idle_timeout(tmp_path):
    head, _ = split_page(1)
    script = session(True, True, FEEDER_AREA, ("S>C", head), gate("never"))
    async with FakeScanner([script]) as srv:
        task = asyncio.create_task(
            feeder_scan(srv, out_dir=str(tmp_path), client={"idle_timeout": 0.4})
        )
        try:
            await abandon(task, srv, "never")
            started = time.monotonic()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
            assert 0.2 < time.monotonic() - started < 1.5
        finally:
            srv.open("never")
    assert os.listdir(tmp_path) == []


async def test_connection_dropped_during_drain_ends_quietly(tmp_path):
    head, _ = split_page(1)
    segs = [("S>C", head), gate("g"), CLOSE]
    async with FakeScanner([session(True, True, FEEDER_AREA, *segs)]) as srv:
        task = asyncio.create_task(feeder_scan(srv, out_dir=str(tmp_path)))
        try:
            await abandon(task, srv, "g")
            srv.open("g")
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
        finally:
            srv.open("g")


async def test_client_side_error_also_drains(tmp_path):
    async def explode(_page, pct):
        if pct == 100:
            raise RuntimeError("callback failed")

    segs = [("S>C", page(1)), gate("g"), ("S>C", page(2) + b"\x80")]
    async with FakeScanner([session(True, True, FEEDER_AREA, *segs)]) as srv:
        task = asyncio.create_task(feeder_scan(srv, out_dir=str(tmp_path), progress=explode))
        try:
            await srv.wait_reached("g")
            await asyncio.sleep(0.2)
            assert not task.done()
            srv.open("g")
            with pytest.raises(RuntimeError):
                await asyncio.wait_for(task, 3)
        finally:
            srv.open("g")
    assert srv.errors == [] and srv.trailing == [b""]
    assert os.listdir(tmp_path) == []


@pytest.mark.parametrize("code", [0xC3, 0xC5])
async def test_device_error_frame_ends_at_once_without_draining(code, tmp_path):
    segs = [("S>C", page(1) + bytes([code])), ("C>S", bp.CMD_RESET), gate("never")]
    async with FakeScanner([session(True, True, FEEDER_AREA, *segs)]) as srv:
        started = time.monotonic()
        try:
            with pytest.raises(ExternalServiceError):
                await asyncio.wait_for(feeder_scan(srv, out_dir=str(tmp_path)), 3)
            assert time.monotonic() - started < 1.5
        finally:
            srv.open("never")
    assert srv.errors == []


async def test_garbled_stream_is_not_drained(tmp_path):
    segs = [("S>C", b"\x7f"), gate("never")]
    async with FakeScanner([session(True, True, FEEDER_AREA, *segs)]) as srv:
        started = time.monotonic()
        try:
            with pytest.raises(bp.BrotherProtocolError):
                await asyncio.wait_for(feeder_scan(srv, out_dir=str(tmp_path)), 3)
            assert time.monotonic() - started < 1.5
        finally:
            srv.open("never")


async def test_glass_cancel_still_drops_at_once(tmp_path):
    area = bp.area_to_pixels(300, 0, 0, 20, 20, 640, 800)
    script = [
        OK,
        ("C>S", bp.encode_lease_request(300, "CGRAY")),
        ("S>C", lease_reply(adf=2, w_px=640, h_px=800)),
        ("C>S", bp.CMD_FEEDER_QUERY),
        ("S>C", bytes([bp.FEEDER_EMPTY])),
        ("C>S", bp.encode_start(300, 300, "CGRAY", area)),
        gate("never"),
    ]
    async with FakeScanner([script]) as srv:
        task = asyncio.create_task(feeder_scan(
            srv, source="flatbed", area_mm=(0, 0, 20, 20), out_dir=str(tmp_path)
        ))
        try:
            await srv.wait_reached("never")
            started = time.monotonic()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
            assert time.monotonic() - started < 0.5
        finally:
            srv.open("never")
