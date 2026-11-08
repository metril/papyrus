import struct

import pytest

from app.exceptions import ScanRequestError
from app.services import brother_protocol as bp
from tests.brother_helpers import (
    MANIFEST,
    NAMES,
    frame,
    lease_reply,
    load,
    page_end,
    split_stream,
    with_page,
)

# (dpi, left, top, width, height) mm -> A, from driver captures.
AREA_TABLE = [
    (300, 0, 0, 50, 50, (0, 0, 560, 565)),
    (300, 20, 30, 50, 50, (208, 329, 800, 920)),
    (100, 0, 0, 50, 50, (0, 0, 192, 188)),
    (150, 0, 0, 50, 50, (0, 0, 288, 282)),
    (200, 0, 0, 50, 50, (0, 0, 384, 377)),
    (400, 0, 0, 50, 50, (0, 0, 752, 754)),
    (600, 0, 0, 50, 50, (0, 0, 1136, 1131)),
    # whole page, clamped to the lease (w_px, h_px)
    (300, 0, 0, 216, 356, (0, 0, 2480, 3437)),
]


@pytest.mark.parametrize("dpi,left,top,w,h,expected", AREA_TABLE)
def test_area_table(dpi, left, top, w, h, expected):
    assert bp.area_to_pixels(dpi, left, top, w, h, 2480 * dpi // 300, 3437 * dpi // 300) == expected


@pytest.mark.parametrize("args", [(300, -1, 0, 50, 50), (300, 0, 0, 0, 50), (300, 0, 0, 50, -5)])
def test_area_rejects_bad_input(args):
    with pytest.raises(ScanRequestError):
        bp.area_to_pixels(*args, 2480, 3437)


def test_area_outside_bed():
    with pytest.raises(ScanRequestError):
        bp.area_to_pixels(300, 400, 0, 10, 10, 2480, 3437)


@pytest.mark.parametrize("dpi", [75, 1200, 0, 301])
def test_resolutions_restricted(dpi):
    with pytest.raises(ScanRequestError):
        bp.check_resolution(dpi)
    with pytest.raises(ScanRequestError):
        bp.encode_lease_request(dpi, "TEXT")


@pytest.mark.parametrize("lease_width, expected", [(826, 816), (1240, 1232), (1653, 1648), (2480, 2480), (3306, 3296), (4960, 4960)])
def test_widths_are_aligned_to_16(lease_width, expected):
    """Real lease widths per dpi: unaligned ones got 1-bit rows a byte short."""
    assert bp.aligned_width(lease_width) == expected
    assert bp.area_to_pixels(300, 0, 0, 9999, 10, lease_width, 3437)[2] == expected


def test_feeder_area_is_full_page():
    lease = bp.parse_lease(lease_reply(adf=1, h_mm=0, h_px=0))
    assert bp.feeder_area(lease) == (0, 0, 2480, 4175)
    lease = bp.parse_lease(lease_reply(600, 600, 1, 209, 4960, 0, 0))
    assert bp.feeder_area(lease) == (0, 0, 4960, 8350)


@pytest.mark.parametrize("name", NAMES)
def test_encoders_match_captured_requests(name):
    segs = load(name)
    cfg = MANIFEST[name]
    sent = [d for dr, d in segs if dr == "C>S"]
    assert sent[0] == bp.encode_lease_request(cfg["dpi"], cfg["mode"])
    assert sent[1] == bp.CMD_FEEDER_QUERY
    scale = cfg["dpi"] // 300 if cfg["dpi"] >= 300 else 1
    area = bp.area_to_pixels(cfg["dpi"], *cfg["area_mm"], 2480 * scale, 3437 * scale)
    assert sent[2] == bp.encode_start(cfg["dpi"], cfg["dpi"], cfg["mode"], area)


def test_parse_lease_examples():
    flat = bp.parse_lease(load("20-fixture-mode-01")[2][1])
    assert (flat.xdpi, flat.ydpi, flat.adf, flat.width_mm) == (300, 300, 2, 209)
    assert (flat.width_px, flat.height_mm, flat.height_px) == (2480, 291, 3437)
    assert not flat.paper_in_feeder
    feeder = bp.parse_lease(b"\x00\x18\x00300,300,1,209,2480,0,0,\x00")
    assert feeder.paper_in_feeder and feeder.height_px == 0
    high = bp.parse_lease(b"\x00\x1d\x00600,1200,2,209,4960,291,6874,\x00")
    assert (high.xdpi, high.ydpi) == (600, 1200)


@pytest.mark.parametrize(
    "data",
    [b"", b"\x00\x00\x00", b"\x00\x05\x00abc\x00", b"\x00\x08\x001,2,3,4,5\x00",
     b"\x00\x0a\x00a,b,c,d,e,f,g\x00", b"\x01\x1d\x00300,300,2,209,2480,291,3437,\x00"],
)
def test_parse_lease_malformed(data):
    with pytest.raises(bp.BrotherProtocolError):
        bp.parse_lease(data)


def test_lease_jam_state_prefix():
    with pytest.raises(bp.BrotherProtocolError) as ei:
        bp.parse_lease(b"\xc5\x1d\x00300,300,2,209,2480,291,3437,\x00")
    assert "jam" in ei.value.detail


def test_packbits():
    assert bp.packbits_decode(bytes.fromhex("bb00"), 100) == b"\0" * 70
    assert bp.packbits_decode(b"\x02abc\x80\xfeZ", 100) == b"abcZZZ"
    assert bp.packbits_decode(b"", 10) == b""


@pytest.mark.parametrize("data", [b"\x05ab", b"\xfe", b"\x00"])
def test_packbits_malformed(data):
    with pytest.raises(bp.BrotherProtocolError):
        bp.packbits_decode(data, 100)


def test_packbits_output_limit():
    with pytest.raises(bp.BrotherProtocolError):
        bp.packbits_decode(bytes.fromhex("bb00"), 69)


def _stream(name):
    segs = load(name)
    return segs[-1][1]


def _collect(parser, data, step=None):
    events = []
    if step is None:
        return parser.feed(data)
    for i in range(0, len(data), step):
        events += parser.feed(data[i : i + step])
    return events


def test_parser_full_fixture():
    events = bp.StreamParser().feed(_stream("20-fixture-mode-01"))
    frames = [e for e in events if isinstance(e, bp.Frame)]
    assert len(frames) == 565 and {f.kind for f in frames} == {bp.KIND_PACKBITS_ROW}
    assert frames[0].page == 1
    assert events[-2:] == [bp.PageEnd(1), bp.JobEnd()]


def test_parser_split_at_every_byte_boundary():
    stream = _stream("20-fixture-mode-01")
    whole = bp.StreamParser().feed(stream)
    assert _collect(bp.StreamParser(), stream, step=1) == whole
    # also every single two-way split of the head and tail
    for cut in list(range(1, 40)) + list(range(len(stream) - 40, len(stream))):
        p = bp.StreamParser()
        assert p.feed(stream[:cut]) + p.feed(stream[cut:]) == whole


@pytest.mark.parametrize("name", NAMES)
def test_parser_all_fixtures(name):
    p = bp.StreamParser()
    events = p.feed(_stream(name))
    assert p.finished and isinstance(events[-1], bp.JobEnd) and isinstance(events[-2], bp.PageEnd)


def test_parser_multi_page():
    frames, _ = split_stream(_stream("20-fixture-mode-01"))
    stream = b"".join(with_page(f, 1) for f in frames[:5]) + page_end(1)
    stream += b"".join(with_page(f, 2) for f in frames[:3]) + page_end(2) + b"\x80"
    events = bp.StreamParser().feed(stream)
    pages = [e.page for e in events if isinstance(e, bp.Frame)]
    assert pages == [1] * 5 + [2] * 3
    assert events[5] == bp.PageEnd(1) and events[-2:] == [bp.PageEnd(2), bp.JobEnd()]


@pytest.mark.parametrize("code", [0xC3, 0xC5])
def test_parser_device_error(code):
    p = bp.StreamParser()
    events = p.feed(frame(bp.KIND_PACKBITS_ROW, 1, b"\xbb\x00") + bytes([code]))
    assert events[-1] == bp.DeviceError(code) and p.finished


def test_parser_unknown_byte_logs_hex_not_detail():
    with pytest.raises(bp.BrotherProtocolError) as ei:
        bp.StreamParser().feed(b"\x99")
    assert "0x99" in ei.value.log_message
    assert "99" not in ei.value.detail


def test_parser_oversize_frame():
    with pytest.raises(bp.BrotherProtocolError):
        bp.StreamParser(max_payload=100).feed(frame(bp.KIND_JPEG, 1, b"x" * 101))
    assert len(bp.StreamParser(max_payload=100).feed(frame(bp.KIND_JPEG, 1, b"x" * 100))) == 1


def test_parser_page_limits():
    row = frame(bp.KIND_RAW_ROW, 1, b"\0" * 4)
    with pytest.raises(bp.BrotherProtocolError):
        bp.StreamParser(max_rows=2).feed(row * 3)
    with pytest.raises(bp.BrotherProtocolError):
        bp.StreamParser(max_page_bytes=10).feed(row * 3)
    with pytest.raises(bp.BrotherProtocolError):
        bp.StreamParser(max_pages=1).feed(row + page_end(1) + with_page(row, 2))


def test_parser_structural_errors():
    row1 = frame(bp.KIND_RAW_ROW, 1, b"\0")
    with pytest.raises(bp.BrotherProtocolError):
        bp.StreamParser().feed(row1 + frame(bp.KIND_RAW_ROW, 2, b"\0"))  # page switch w/o end
    with pytest.raises(bp.BrotherProtocolError):
        bp.StreamParser().feed(row1 + frame(bp.KIND_JPEG, 1, b"\0"))  # mixed kinds
    with pytest.raises(bp.BrotherProtocolError):
        bp.StreamParser().feed(row1 + b"\x80")  # job end inside a page
    with pytest.raises(bp.BrotherProtocolError):
        bp.StreamParser().feed(row1 + page_end(2))
    p = bp.StreamParser()
    p.feed(b"\x80")
    with pytest.raises(bp.BrotherProtocolError):
        p.feed(b"\x00")


def test_error_str_is_client_detail():
    with pytest.raises(bp.BrotherProtocolError) as ei:
        bp.StreamParser().feed(b"\xee")
    assert str(ei.value) == ei.value.detail


def test_struct_formats_are_little_endian():
    assert struct.unpack_from("<H", frame(bp.KIND_JPEG, 0x0102, b"ab"), 3) == (0x0102,)
