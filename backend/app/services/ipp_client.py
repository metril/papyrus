"""Minimal, hand-rolled IPP (Internet Printing Protocol) client.

Papyrus needs to ask arbitrary network printers for their attributes
(make/model, location, marker/supply levels, printer-state, ...) both when a
user probes/discovers a device and, later, for supply-level alerts. None of
the obvious libraries fit: ``pycups`` only talks to the local ``cupsd`` over
its domain socket, ``ipptool`` is not installed in the container, and
``pyipp`` pulls in ``aiohttp`` (we standardise on the shared ``httpx`` pool).

So this module implements just enough of IPP/1.1 to issue a single
``Get-Printer-Attributes`` operation and decode the response's attribute
groups. It intentionally supports nothing else.

Design constraints:
- Outbound HTTP goes through the shared pooled client (``get_http_client()``),
  fetched at request time, with a per-request ``timeout=``.
- The decoder is deliberately tolerant: printers vary wildly and send
  attribute types we do not model. An unknown value tag, an attribute that
  fails to decode, or a body that is truncated after some attributes were
  already read must never abort the whole parse — we keep what we could read.
  A body truncated before *any* attribute was decoded (or a malformed header,
  or an IPP error status) raises ``IppError``.

IPP wire format reference (RFC 8010): a request/response is
``version(2) + operation-id-or-status(2) + request-id(4)`` followed by
attribute groups. Each group starts with a delimiter tag (0x00-0x0F) and
contains attributes; each attribute is
``value-tag(1) + name-length(2, BE) + name + value-length(2, BE) + value``.
A zero name-length marks an additional value of the preceding attribute
(a "1setOf" member). The stream ends with the end-of-attributes tag 0x03.
"""

import httpx

from app.services.http_client import get_http_client

# Operation / delimiter tags.
_GET_PRINTER_ATTRIBUTES = 0x000B
_OPERATION_ATTRIBUTES_TAG = 0x01
_END_OF_ATTRIBUTES_TAG = 0x03

# Value tags we interpret. Everything in 0x00-0x0F is a group delimiter; a
# value tag is always >= 0x10.
_TAG_INTEGER = 0x21
_TAG_BOOLEAN = 0x22
_TAG_ENUM = 0x23

# Attributes we ask every printer for. The first is sent as a normal keyword
# value; the rest ride along as a 1setOf (zero-length name) continuation.
_REQUESTED_ATTRIBUTES = [
    "printer-make-and-model",
    "printer-location",
    "printer-info",
    "printer-uuid",
    "printer-state",
    "printer-state-reasons",
    "marker-names",
    "marker-levels",
    "marker-colors",
    "marker-types",
    "marker-high-levels",
    "marker-low-levels",
    "printer-supply",
    "printer-supply-description",
    "media-default",
    "sides-supported",
]


class IppError(Exception):
    """Raised on connect failure, non-2xx HTTP, an undecodable body, or an IPP
    status-code >= 0x0400."""


class _DecodeError(Exception):
    """Internal: a single attribute value could not be decoded and should be
    skipped without aborting the rest of the parse."""


# --------------------------------------------------------------------------- #
# Encoder
# --------------------------------------------------------------------------- #
def _encode_attribute(tag: int, name: str, value: bytes) -> bytes:
    name_b = name.encode("ascii")
    return (
        bytes([tag])
        + len(name_b).to_bytes(2, "big")
        + name_b
        + len(value).to_bytes(2, "big")
        + value
    )


def _encode_request(host: str, port: int, resource: str) -> bytes:
    """Build a Get-Printer-Attributes request for ``ipp://host:port{resource}``."""
    out = bytearray()
    out += bytes([0x01, 0x01])  # IPP version 1.1
    out += _GET_PRINTER_ATTRIBUTES.to_bytes(2, "big")  # operation-id
    out += (1).to_bytes(4, "big")  # request-id
    out += bytes([_OPERATION_ATTRIBUTES_TAG])

    out += _encode_attribute(0x47, "attributes-charset", b"utf-8")  # charset
    out += _encode_attribute(0x48, "attributes-natural-language", b"en")  # naturalLanguage
    printer_uri = f"ipp://{host}:{port}{resource}".encode("utf-8")
    out += _encode_attribute(0x45, "printer-uri", printer_uri)  # uri

    first, *rest = _REQUESTED_ATTRIBUTES
    out += _encode_attribute(0x44, "requested-attributes", first.encode("ascii"))  # keyword
    for attr in rest:  # 1setOf continuation: zero-length names
        out += _encode_attribute(0x44, "", attr.encode("ascii"))

    out += bytes([_END_OF_ATTRIBUTES_TAG])
    return bytes(out)


# --------------------------------------------------------------------------- #
# Decoder
# --------------------------------------------------------------------------- #
def _decode_value(tag: int, raw: bytes):
    if tag in (_TAG_INTEGER, _TAG_ENUM):
        if len(raw) != 4:
            raise _DecodeError
        return int.from_bytes(raw, "big", signed=True)
    if tag == _TAG_BOOLEAN:
        if len(raw) != 1:
            raise _DecodeError
        return raw[0] != 0
    if 0x41 <= tag <= 0x49:  # text/name/keyword/uri/charset/naturalLanguage/mimeMediaType
        return raw.decode("utf-8", errors="replace")
    if 0x10 <= tag <= 0x1F or 0x30 <= tag <= 0x3F:
        # Recognised but unmodelled types (out-of-band, octetString, dateTime,
        # resolution, rangeOfInteger, collections, ...) — keep the raw bytes.
        return raw
    raise _DecodeError  # genuinely unknown value tag — skip it


def _append_value(attributes: dict, last_name: str | None, value) -> None:
    if last_name is None or last_name not in attributes:
        return  # nothing to attach this 1setOf continuation to
    existing = attributes[last_name]
    if isinstance(existing, list):
        existing.append(value)
    else:
        attributes[last_name] = [existing, value]


def _decode_response(data: bytes) -> dict:
    """Decode an IPP response body into ``{attr-name: value | [values]}``.

    Raises ``IppError`` on a malformed header, an IPP error status
    (>= 0x0400), or a body truncated before any attribute could be read.
    """
    if len(data) < 8:
        raise IppError("IPP response too short to contain a header")
    status_code = int.from_bytes(data[2:4], "big")
    if status_code >= 0x0400:
        raise IppError(f"IPP returned error status 0x{status_code:04x}")

    attributes: dict = {}
    last_name: str | None = None
    pos = 8
    length = len(data)
    truncated = False

    while pos < length:
        tag = data[pos]
        pos += 1
        if tag == _END_OF_ATTRIBUTES_TAG:
            break
        if tag <= 0x0F:  # group delimiter — a new group breaks 1setOf grouping
            last_name = None
            continue

        # Attribute: value-tag + name-length + name + value-length + value.
        if pos + 2 > length:
            truncated = True
            break
        name_len = int.from_bytes(data[pos:pos + 2], "big")
        pos += 2
        if pos + name_len > length:
            truncated = True
            break
        name = data[pos:pos + name_len].decode("utf-8", errors="replace")
        pos += name_len
        if pos + 2 > length:
            truncated = True
            break
        value_len = int.from_bytes(data[pos:pos + 2], "big")
        pos += 2
        if pos + value_len > length:
            truncated = True
            break
        raw_value = data[pos:pos + value_len]
        pos += value_len

        try:
            value = _decode_value(tag, raw_value)
        except _DecodeError:
            # Skip this value but stay byte-aligned. If it was a *named*
            # attribute, remember the name so stray continuations don't attach
            # to whatever was decoded before it.
            if name_len:
                last_name = name
            continue

        if name_len == 0:
            _append_value(attributes, last_name, value)
        else:
            attributes[name] = value
            last_name = name

    if truncated and not attributes:
        raise IppError("IPP response truncated before any attribute was decoded")
    return attributes


# --------------------------------------------------------------------------- #
# Normalisation (for probe_ipp)
# --------------------------------------------------------------------------- #
def _first(value):
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _as_str(value) -> str | None:
    v = _first(value)
    return v if isinstance(v, str) else None


def _as_int(value) -> int | None:
    v = _first(value)
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _as_list(value) -> list:
    if value is None:
        return []
    return list(value) if isinstance(value, list) else [value]


def _normalize(attributes: dict, resource: str) -> dict:
    return {
        "make_and_model": _as_str(attributes.get("printer-make-and-model")),
        "location": _as_str(attributes.get("printer-location")),
        "info": _as_str(attributes.get("printer-info")),
        "uuid": _as_str(attributes.get("printer-uuid")),
        "state": _as_int(attributes.get("printer-state")),
        "state_reasons": _as_list(attributes.get("printer-state-reasons")),
        "markers": {
            "names": _as_list(attributes.get("marker-names")),
            "levels": _as_list(attributes.get("marker-levels")),
            "colors": _as_list(attributes.get("marker-colors")),
            "types": _as_list(attributes.get("marker-types")),
            "high_levels": _as_list(attributes.get("marker-high-levels")),
            "low_levels": _as_list(attributes.get("marker-low-levels")),
        },
        "media_default": _as_str(attributes.get("media-default")),
        "sides_supported": _as_list(attributes.get("sides-supported")),
        "resource": resource,
        "attributes": attributes,
    }


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
async def get_printer_attributes(
    host: str,
    port: int = 631,
    resource: str = "/ipp/print",
    timeout: float = 5.0,
) -> dict:
    """Issue Get-Printer-Attributes and return the raw decoded attribute dict.

    Raises ``IppError`` on transport failure, non-2xx HTTP, or an undecodable
    body / IPP error status.
    """
    request_body = _encode_request(host, port, resource)
    # An empty resource still needs a valid HTTP path, so POST to "/".
    url = f"http://{host}:{port}{resource or '/'}"
    client = get_http_client()
    try:
        response = await client.post(
            url,
            content=request_body,
            headers={"Content-Type": "application/ipp"},
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise IppError(f"IPP request to {url} failed: {exc}") from exc
    if not (200 <= response.status_code < 300):
        raise IppError(f"IPP request to {url} returned HTTP {response.status_code}")
    return _decode_response(response.content)


async def probe_ipp(host: str, port: int = 631, timeout: float = 5.0) -> dict | None:
    """Probe common IPP resource paths and return a normalised attribute dict.

    Tries ``/ipp/print``, ``/ipp``, then ``""`` in order and returns the
    normalised result from the first success, or ``None`` if none respond.
    Never raises.
    """
    for resource in ("/ipp/print", "/ipp", ""):
        try:
            attributes = await get_printer_attributes(host, port, resource, timeout)
        except IppError:
            continue
        return _normalize(attributes, resource)
    return None
