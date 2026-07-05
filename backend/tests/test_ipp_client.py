"""Tests for the hand-rolled minimal IPP client.

No live printer is available, so every case is driven by hand-constructed IPP
wire bytes: the encoder is exercised by parsing back its own output, the
decoder by feeding it fixture response bodies (including malformed/truncated
ones), and the transport/probe layers by monkeypatching the shared httpx
client with an ``httpx.MockTransport``.
"""
import httpx
import pytest

import app.services.http_client as http_client_module
from app.services import ipp_client
from app.services.ipp_client import (
    IppError,
    _decode_response,
    _encode_request,
    get_printer_attributes,
    probe_ipp,
)


# --------------------------------------------------------------------------- #
# Byte-fixture helpers
# --------------------------------------------------------------------------- #
def _u16(n: int) -> bytes:
    return n.to_bytes(2, "big")


def _i32(n: int) -> bytes:
    return n.to_bytes(4, "big", signed=True)


def _attr(tag: int, name, value) -> bytes:
    name_b = name.encode() if isinstance(name, str) else name
    value_b = value.encode() if isinstance(value, str) else bytes(value)
    return bytes([tag]) + _u16(len(name_b)) + name_b + _u16(len(value_b)) + value_b


def _resp_header(status: int = 0x0000) -> bytes:
    # version 1.1, status-code, request-id 1
    return b"\x01\x01" + _u16(status) + b"\x00\x00\x00\x01"


def _sample_response() -> bytes:
    body = bytearray()
    body += _resp_header(0x0000)
    body += bytes([0x04])  # printer-attributes group delimiter
    body += _attr(0x41, "printer-make-and-model", "Brother DCP-L2540DW")
    body += _attr(0x41, "printer-location", "Front Office")
    body += _attr(0x23, "printer-state", _i32(3))  # enum idle
    body += _attr(0x21, "marker-levels", _i32(80))
    body += _attr(0x21, "", _i32(60))  # additional 1setOf value
    body += _attr(0x21, "", _i32(-1))  # additional 1setOf value (unknown level)
    body += _attr(0x44, "sides-supported", "one-sided")
    body += _attr(0x44, "", "two-sided-long-edge")
    body += bytes([0x03])  # end-of-attributes
    return bytes(body)


def _parse_operation_attrs(req: bytes):
    """Walk the operation-attributes group of an encoded request."""
    pos = 9  # 8-byte header + 1-byte operation-attributes group tag
    attrs = []
    while pos < len(req):
        tag = req[pos]
        pos += 1
        if tag == 0x03:
            break
        name_len = int.from_bytes(req[pos:pos + 2], "big")
        pos += 2
        name = req[pos:pos + name_len]
        pos += name_len
        value_len = int.from_bytes(req[pos:pos + 2], "big")
        pos += 2
        value = req[pos:pos + value_len]
        pos += value_len
        attrs.append((tag, name, value))
    return attrs


@pytest.fixture(autouse=True)
async def _reset_http_client():
    """Each transport test installs a MockTransport onto the shared client;
    reset back to None afterwards so other tests get a fresh real client."""
    yield
    if http_client_module._client is not None:
        await http_client_module._client.aclose()
    http_client_module._client = None


def _install_transport(handler) -> None:
    http_client_module._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --------------------------------------------------------------------------- #
# Encoder
# --------------------------------------------------------------------------- #
def test_encode_request_header_is_get_printer_attributes():
    req = _encode_request("192.0.2.10", 631, "/ipp/print")
    assert req[0:2] == b"\x01\x01"          # IPP version 1.1
    assert req[2:4] == b"\x00\x0b"          # operation-id Get-Printer-Attributes
    assert req[4:8] == b"\x00\x00\x00\x01"  # request-id 1
    assert req[8] == 0x01                   # operation-attributes group tag
    assert req[-1] == 0x03                  # end-of-attributes tag


def test_encode_request_operation_attributes_in_order():
    req = _encode_request("192.0.2.10", 631, "/ipp/print")
    attrs = _parse_operation_attrs(req)
    assert attrs[0] == (0x47, b"attributes-charset", b"utf-8")
    assert attrs[1] == (0x48, b"attributes-natural-language", b"en")
    assert attrs[2] == (0x45, b"printer-uri", b"ipp://192.0.2.10:631/ipp/print")


def test_encode_request_requested_attributes_are_1setof_with_zero_length_names():
    req = _encode_request("192.0.2.10", 631, "/ipp/print")
    attrs = _parse_operation_attrs(req)
    requested = attrs[3:]
    # First requested-attributes value carries the name; the rest are a 1setOf
    # continuation encoded with a zero-length name.
    assert requested[0] == (0x44, b"requested-attributes", b"printer-make-and-model")
    assert all(tag == 0x44 for tag, _, _ in requested)
    assert all(name == b"" for _, name, _ in requested[1:])
    values = [value for _, _, value in requested]
    assert b"printer-uuid" in values
    assert b"marker-levels" in values
    assert b"sides-supported" in values
    assert len(requested) == 16  # every requested attribute is present


def test_encode_request_empty_resource_has_no_trailing_slash_in_uri():
    req = _encode_request("192.0.2.10", 631, "")
    attrs = _parse_operation_attrs(req)
    assert attrs[2] == (0x45, b"printer-uri", b"ipp://192.0.2.10:631")


# --------------------------------------------------------------------------- #
# Decoder
# --------------------------------------------------------------------------- #
def test_decode_full_response():
    result = _decode_response(_sample_response())
    assert result["printer-make-and-model"] == "Brother DCP-L2540DW"
    assert result["printer-location"] == "Front Office"
    assert result["printer-state"] == 3
    assert result["marker-levels"] == [80, 60, -1]
    assert result["sides-supported"] == ["one-sided", "two-sided-long-edge"]


def test_decode_skips_unknown_value_tag_and_parses_the_rest():
    body = bytearray()
    body += _resp_header(0x0000)
    body += bytes([0x04])
    body += _attr(0x41, "printer-make-and-model", "Brother")
    body += _attr(0x60, "weird-attr", b"\x00\x01\x02")  # unknown value tag
    body += _attr(0x41, "printer-location", "Office")
    body += bytes([0x03])

    result = _decode_response(bytes(body))
    assert result["printer-make-and-model"] == "Brother"
    assert result["printer-location"] == "Office"
    assert "weird-attr" not in result


def test_decode_truncated_after_some_attrs_returns_partial():
    body = bytearray()
    body += _resp_header(0x0000)
    body += bytes([0x04])
    body += _attr(0x41, "printer-make-and-model", "Brother")
    # A second attribute that claims an 8-byte name but supplies only 3 bytes.
    body += bytes([0x41]) + _u16(8) + b"loc"

    result = _decode_response(bytes(body))
    assert result == {"printer-make-and-model": "Brother"}


def test_decode_truncated_with_nothing_decoded_raises():
    body = bytearray()
    body += _resp_header(0x0000)
    body += bytes([0x04])
    body += bytes([0x41]) + _u16(8) + b"lo"  # truncated before any value decoded

    with pytest.raises(IppError):
        _decode_response(bytes(body))


def test_decode_short_header_raises():
    with pytest.raises(IppError):
        _decode_response(b"\x01\x01\x00")


def test_decode_error_status_raises():
    body = _resp_header(0x0400) + bytes([0x03])
    with pytest.raises(IppError):
        _decode_response(body)


def test_decode_keeps_raw_bytes_for_datetime_tag():
    body = bytearray()
    body += _resp_header(0x0000)
    body += bytes([0x04])
    body += _attr(0x31, "printer-current-time", b"\x07\xe6\x01\x02")  # dateTime
    body += bytes([0x03])
    result = _decode_response(bytes(body))
    assert result["printer-current-time"] == b"\x07\xe6\x01\x02"


# --------------------------------------------------------------------------- #
# Transport (get_printer_attributes)
# --------------------------------------------------------------------------- #
async def test_get_printer_attributes_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Content-Type"] == "application/ipp"
        assert str(request.url) == "http://192.0.2.10:631/ipp/print"
        return httpx.Response(200, content=_sample_response())

    _install_transport(handler)
    result = await get_printer_attributes("192.0.2.10")
    assert result["printer-make-and-model"] == "Brother DCP-L2540DW"


async def test_get_printer_attributes_empty_resource_posts_to_root():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, content=_sample_response())

    _install_transport(handler)
    await get_printer_attributes("192.0.2.10", resource="")
    assert seen["path"] == "/"


async def test_get_printer_attributes_non_2xx_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    _install_transport(handler)
    with pytest.raises(IppError):
        await get_printer_attributes("192.0.2.10")


async def test_get_printer_attributes_connect_error_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    _install_transport(handler)
    with pytest.raises(IppError):
        await get_printer_attributes("192.0.2.10")


# --------------------------------------------------------------------------- #
# probe_ipp
# --------------------------------------------------------------------------- #
async def test_probe_ipp_falls_back_to_second_resource():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/ipp/print":
            return httpx.Response(404)
        if request.url.path == "/ipp":
            return httpx.Response(200, content=_sample_response())
        return httpx.Response(404)

    _install_transport(handler)
    result = await probe_ipp("192.0.2.10")
    assert result is not None
    assert result["resource"] == "/ipp"
    assert result["make_and_model"] == "Brother DCP-L2540DW"
    assert result["location"] == "Front Office"
    assert result["state"] == 3
    assert result["markers"]["levels"] == [80, 60, -1]
    assert result["sides_supported"] == ["one-sided", "two-sided-long-edge"]
    assert result["attributes"]["printer-make-and-model"] == "Brother DCP-L2540DW"


async def test_probe_ipp_all_resources_fail_returns_none():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    _install_transport(handler)
    assert await probe_ipp("192.0.2.10") is None


async def test_probe_ipp_never_raises_on_connect_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    _install_transport(handler)
    assert await probe_ipp("192.0.2.10") is None


def test_module_exposes_public_api():
    assert callable(ipp_client.get_printer_attributes)
    assert callable(ipp_client.probe_ipp)
    assert issubclass(ipp_client.IppError, Exception)
