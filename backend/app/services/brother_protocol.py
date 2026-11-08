"""Wire protocol for Brother network scanners (TCP 54921).

Pure functions and a state machine: no I/O, no asyncio. The socket layer lives
in ``brother_client``. Everything here is derived from our own captures.

Stream layout after a start request (``ESC X``): a sequence of frames
``kind(1) header(9) len(u16 LE at offset 10) payload(at 12)`` where kind is
0x42 (PackBits row), 0x40 (raw row) or 0x64 (JPEG chunk); each page ends with
a 10-byte ``0x82`` marker and the job with a single ``0x80``. A status byte
(``0xc3``/``0xc5``) in place of a frame means the device failed.
"""
from __future__ import annotations

import logging
import math
import struct
from dataclasses import dataclass

from app.exceptions import ExternalServiceError, ScanRequestError

logger = logging.getLogger(__name__)

PORT = 54921
GREETING_OK = b"+OK 200\r\n"
GREETING_BUSY = b"-NG 401\r\n"

RESOLUTIONS = (100, 150, 200, 300, 400, 600)

# Physical margin the driver removes from the glass origin (mm). Fitted against
# the driver captures: y = floor((mm - margin) * dpi / 25.4), x the same value
# rounded to the nearest multiple of 16.
MARGIN_MM = 2.1
FEEDER_LENGTH_MM = 353.5

KIND_RAW_ROW = 0x40
KIND_PACKBITS_ROW = 0x42
KIND_JPEG = 0x64
KIND_PAGE_END = 0x82
KIND_JOB_END = 0x80
ROW_KINDS = (KIND_RAW_ROW, KIND_PACKBITS_ROW)
DATA_KINDS = (*ROW_KINDS, KIND_JPEG)
ERROR_CODES = (0xC3, 0xC5)

FEEDER_LOADED = 0x80
FEEDER_EMPTY = 0xC2

# Commands. ESC R (reset) is not in our captures; the framing follows ESC Q/D/I.
CMD_FEEDER_QUERY = b"\x1bD\nADF\n\x80"
CMD_RESET = b"\x1bR\n\x80"

_FRAME_HEADER = 12
_PAGE_END_LEN = 10


class BrotherProtocolError(ExternalServiceError):
    """The device sent something we cannot parse.

    ``detail`` is client-safe; ``log_message`` may carry raw bytes and is for
    logs only.
    """

    def __init__(self, log_message: str, detail: str = "The scanner sent an unexpected response."):
        super().__init__(detail)
        self.log_message = log_message


@dataclass(frozen=True)
class ModeSpec:
    name: str
    code: str  # C= value
    bits: int  # per pixel in row modes; 24 for JPEG
    pil_mode: str
    invert: bool

    @property
    def jpeg(self) -> bool:
        return self.code == "JPEG"

    def row_bytes(self, width_px: int) -> int:
        return (width_px + 7) // 8 if self.bits == 1 else width_px


MODES: dict[str, ModeSpec] = {
    "TEXT": ModeSpec("TEXT", "RLENGTH", 1, "1", True),
    "ERRDIF": ModeSpec("ERRDIF", "RLENGTH", 1, "1", True),
    "GRAY64": ModeSpec("GRAY64", "RLENGTH", 8, "L", False),
    "CGRAY": ModeSpec("CGRAY", "JPEG", 24, "RGB", False),
}


def get_mode(name: str) -> ModeSpec:
    try:
        return MODES[name]
    except KeyError:
        raise ScanRequestError("Unsupported scan mode.") from None


def check_resolution(dpi: int) -> int:
    if dpi not in RESOLUTIONS:
        raise ScanRequestError(
            "Unsupported resolution. Choose one of: " + ", ".join(map(str, RESOLUTIONS)) + " dpi."
        )
    return dpi


# ---------------------------------------------------------------------------
# Encoders
# ---------------------------------------------------------------------------

def encode_lease_request(dpi: int, mode: str) -> bytes:
    check_resolution(dpi)
    get_mode(mode)
    return f"\x1bI\nR={dpi},{dpi}\nM={mode}\n\x80".encode("latin-1")


def encode_start(xdpi: int, ydpi: int, mode: str, area: tuple[int, int, int, int]) -> bytes:
    spec = get_mode(mode)
    x1, y1, x2, y2 = area
    out = f"\x1bX\nR={xdpi},{ydpi}\nM={mode}\nC={spec.code}\n"
    if spec.jpeg:
        out += "J=MID\n"
    out += f"B=50\nN=50\nA={x1},{y1},{x2},{y2}\nS=NORMAL_SCAN\nP=0\nG=0\nL=0\n\x80"
    return out.encode("latin-1")


# ---------------------------------------------------------------------------
# Lease
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Lease:
    xdpi: int
    ydpi: int
    adf: int  # 1 = paper in feeder, 2 = flatbed
    width_mm: int
    width_px: int
    height_mm: int
    height_px: int

    @property
    def paper_in_feeder(self) -> bool:
        return self.adf == 1


def parse_lease(data: bytes) -> Lease:
    """Parse a full lease reply (3 prefix bytes + ASCII body).

    Raises ``BrotherProtocolError`` for jam-state or malformed replies. An empty
    reply is the caller's business (it means "busy").
    """
    if len(data) < 4:
        raise BrotherProtocolError(f"short lease reply: {data.hex()}")
    if data[0] in ERROR_CODES:
        raise BrotherProtocolError(
            f"lease reply flagged device error 0x{data[0]:02x}",
            "The scanner reports a problem (possibly a paper jam). Check the scanner.",
        )
    if data[0] != 0 or data[2] != 0:
        raise BrotherProtocolError(f"unexpected lease prefix: {data[:3].hex()}")
    body = data[3:].split(b"\0", 1)[0]
    try:
        parts = [int(p) for p in body.decode("ascii").rstrip(",").split(",")]
    except (UnicodeDecodeError, ValueError):
        raise BrotherProtocolError(f"unparseable lease body: {body[:64]!r}") from None
    if len(parts) != 7 or any(p < 0 or p > 100000 for p in parts):
        raise BrotherProtocolError(f"bad lease fields: {body[:64]!r}")
    lease = Lease(*parts)
    if lease.xdpi == 0 or lease.ydpi == 0 or lease.width_px == 0:
        raise BrotherProtocolError(f"degenerate lease: {body[:64]!r}")
    return lease


# ---------------------------------------------------------------------------
# Area conversion
# ---------------------------------------------------------------------------

def _edge_px(mm: float, dpi: int) -> float:
    return max(0.0, mm - MARGIN_MM) * dpi / 25.4


def area_to_pixels(
    dpi: int,
    left_mm: float,
    top_mm: float,
    width_mm: float,
    height_mm: float,
    max_x: int,
    max_y: int,
) -> tuple[int, int, int, int]:
    """Convert a scan area in mm to the ``A=x1,y1,x2,y2`` pixel box.

    Each edge is shifted by the glass margin; y edges are floored, x edges are
    rounded to the nearest multiple of 16. The box is clamped to the lease.
    """
    check_resolution(dpi)
    if min(left_mm, top_mm) < 0 or width_mm <= 0 or height_mm <= 0:
        raise ScanRequestError("Invalid scan area.")

    def round16(v: float) -> int:
        return int(math.floor(v / 16 + 0.5)) * 16

    x1 = round16(_edge_px(left_mm, dpi))
    x2 = min(round16(_edge_px(left_mm + width_mm, dpi)), max_x)
    y1 = int(math.floor(_edge_px(top_mm, dpi) + 1e-6))
    y2 = min(int(math.floor(_edge_px(top_mm + height_mm, dpi) + 1e-6)), max_y)
    if x2 <= x1 or y2 <= y1:
        raise ScanRequestError("The scan area is outside the scanner's bed.")
    return x1, y1, x2, y2


def feeder_area(lease: Lease) -> tuple[int, int, int, int]:
    """Feeder scans always use the full width and length; never a partial area."""
    return 0, 0, lease.width_px, int(FEEDER_LENGTH_MM * lease.ydpi / 25.4 + 1e-6)


# ---------------------------------------------------------------------------
# PackBits
# ---------------------------------------------------------------------------

def packbits_decode(data: bytes, max_out: int) -> bytes:
    """Decode PackBits, rejecting truncated runs and output over ``max_out``."""
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        c = data[i]
        i += 1
        if c < 128:
            end = i + c + 1
            if end > n:
                raise BrotherProtocolError("PackBits literal run overruns the frame")
            out += data[i:end]
            i = end
        elif c > 128:
            if i >= n:
                raise BrotherProtocolError("PackBits repeat run missing its byte")
            out += data[i : i + 1] * (257 - c)
            i += 1
        if len(out) > max_out:
            raise BrotherProtocolError(f"PackBits output exceeds {max_out} bytes")
    return bytes(out)


# ---------------------------------------------------------------------------
# Incremental stream parser
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Frame:
    page: int
    kind: int
    payload: bytes


@dataclass(frozen=True)
class PageEnd:
    page: int


@dataclass(frozen=True)
class JobEnd:
    pass


@dataclass(frozen=True)
class DeviceError:
    code: int


Event = Frame | PageEnd | JobEnd | DeviceError


class StreamParser:
    """Turns arbitrary byte chunks into events; any split point is safe."""

    def __init__(
        self,
        *,
        max_payload: int = 65535,
        max_page_bytes: int = 64 * 1024 * 1024,
        max_rows: int = 40000,
        max_pages: int = 200,
    ) -> None:
        self._buf = bytearray()
        self._max_payload = max_payload
        self._max_page_bytes = max_page_bytes
        self._max_rows = max_rows
        self._max_pages = max_pages
        self._page: int | None = None
        self._page_kind: int | None = None
        self._page_bytes = 0
        self._page_rows = 0
        self._pages_seen = 0
        self.finished = False

    def feed(self, data: bytes) -> list[Event]:
        if self.finished:
            if data:
                raise BrotherProtocolError(f"{len(data)} bytes after end of stream")
            return []
        self._buf += data
        events: list[Event] = []
        while self._buf and not self.finished:
            ev = self._next()
            if ev is None:
                break
            events.append(ev)
        if self.finished and self._buf:
            raise BrotherProtocolError(f"{len(self._buf)} bytes after end of stream")
        return events

    def _next(self) -> Event | None:
        buf = self._buf
        kind = buf[0]
        if kind in DATA_KINDS:
            if len(buf) < _FRAME_HEADER:
                return None
            (length,) = struct.unpack_from("<H", buf, 10)
            if length > self._max_payload:
                raise BrotherProtocolError(
                    f"frame payload {length} exceeds limit {self._max_payload}"
                )
            if len(buf) < _FRAME_HEADER + length:
                return None
            (page,) = struct.unpack_from("<H", buf, 3)
            payload = bytes(buf[_FRAME_HEADER : _FRAME_HEADER + length])
            self._check_frame(kind, page, length)
            del buf[: _FRAME_HEADER + length]
            return Frame(page, kind, payload)
        if kind == KIND_PAGE_END:
            if len(buf) < _PAGE_END_LEN:
                return None
            (page,) = struct.unpack_from("<H", buf, 3)
            del buf[:_PAGE_END_LEN]
            if self._page is None:
                self._begin_page(page, None)
            elif page != self._page:
                raise BrotherProtocolError(f"page end {page} while page {self._page} is open")
            self._page = None
            self._page_kind = None
            return PageEnd(page)
        if kind == KIND_JOB_END:
            if self._page is not None:
                raise BrotherProtocolError("job end inside an open page")
            del buf[:1]
            self.finished = True
            return JobEnd()
        if kind in ERROR_CODES:
            del buf[:1]
            self.finished = True
            return DeviceError(kind)
        raise BrotherProtocolError(f"unknown stream byte 0x{kind:02x}")

    def _begin_page(self, page: int, kind: int | None) -> None:
        self._pages_seen += 1
        if self._pages_seen > self._max_pages:
            raise BrotherProtocolError(f"more than {self._max_pages} pages")
        self._page, self._page_kind = page, kind
        self._page_bytes = self._page_rows = 0

    def _check_frame(self, kind: int, page: int, length: int) -> None:
        if self._page is None:
            self._begin_page(page, kind)
        elif page != self._page:
            raise BrotherProtocolError(f"frame for page {page} while page {self._page} is open")
        elif kind != self._page_kind:
            raise BrotherProtocolError(
                f"frame kind 0x{kind:02x} mixed into page of 0x{self._page_kind:02x}"
            )
        self._page_bytes += length
        if self._page_bytes > self._max_page_bytes:
            raise BrotherProtocolError(f"page exceeds {self._max_page_bytes} bytes")
        if kind in ROW_KINDS:
            self._page_rows += 1
            if self._page_rows > self._max_rows:
                raise BrotherProtocolError(f"page exceeds {self._max_rows} rows")
