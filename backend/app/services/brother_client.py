"""asyncio client for Brother network scanners (see ``brother_protocol``).

One ``scan()`` call is one connection: handshake (with bounded retries while
the device is busy), safety checks on the feeder, then the page stream, each
page spooled to disk and converted to a lossless TIFF. Host validation is the
caller's job. A feeder request always pulls every sheet and hanging up does
not stop it (it may jam the printer), so an abandoned feeder stream is drained
to its job-end marker before the connection is closed.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.exceptions import (
    ExternalServiceError,
    ScannerBusyError,
    ScanRequestError,
    ScanSourceError,
)
from app.services import brother_protocol as bp

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int], Awaitable[None]]  # (page, percent)

_BUSY_DETAIL = "The scanner is busy. Try again in a moment."
_TRUNCATED_LOCK = threading.Lock()
_MAX_PAGE_BYTES = 128 * 1024 * 1024
_FILL_VALUE = 128
_FILL_EDGE_PIXELS = 16  # one JPEG block
# Overall cap on reading out an abandoned feeder stream: a full 35-sheet feeder at
# the slowest setting (600 dpi colour, roughly 45 s a sheet) plus margin. The idle
# timeout alone never ends a device that keeps trickling bytes.
_DRAIN_DEADLINE_SECONDS = 35 * 45 + 225


@dataclass(frozen=True)
class ScannedPage:
    path: str
    width: int
    height: int
    dpi: int
    mode: str  # PIL mode of the TIFF
    from_feeder: bool = False  # the printer served this from the feeder


FEEDER_EMPTY_DETAIL = (
    "The document feeder is empty. Load paper in the feeder, or scan from the glass."
)


@dataclass(frozen=True)
class FeederReading:
    """The two paper signals the scan's own source check uses."""

    query_paper: bool  # ESC D reply
    lease_paper: bool  # lease says paper in feeder

    @property
    def loaded(self) -> bool:
        return self.query_paper and self.lease_paper

    @property
    def takes_over_glass(self) -> bool:
        """Either signal means the printer feeds instead of scanning the glass."""
        return self.query_paper or self.lease_paper


def _invert_table() -> bytes:
    return bytes(255 - i for i in range(256))


_INVERT = _invert_table()


def _trim_fill_rows(img):
    """Drop trailing rows the decoder filled in.

    The data can stop partway across a row of JPEG blocks, leaving the rest of
    that band filled through to the right edge, so the right edge decides.
    """
    import numpy as np

    arr = np.asarray(img)
    filled = (arr[:, -_FILL_EDGE_PIXELS:] == _FILL_VALUE).all(axis=(1, 2))
    keep = len(filled)
    while keep > 0 and filled[keep - 1]:
        keep -= 1
    return img.crop((0, 0, img.width, keep)) if keep != len(filled) else img


def _pad_white(img, height: int):
    from PIL import Image

    if img.height >= height:
        return img
    white = 1 if img.mode == "1" else (255,) * len(img.getbands())
    out = Image.new(img.mode, (img.width, height), white)
    out.paste(img, (0, 0))
    return out


def _finish_raw_page(spool: str, out: str, mode: bp.ModeSpec, width: int, rows: int,
                     want_height: int | None, dpi: tuple[int, int]) -> tuple[int, int]:
    from PIL import Image

    with open(spool, "rb") as fh:
        raw = fh.read()
    img = Image.frombytes(mode.pil_mode, (width, rows), raw)
    if want_height:
        img = _pad_white(img, want_height)
    img.save(out, format="TIFF", compression="tiff_adobe_deflate", dpi=dpi)
    return img.width, img.height


def _finish_jpeg_page(spool: str, out: str, trim: bool, want_height: int | None,
                      dpi: tuple[int, int]) -> tuple[int, int]:
    from PIL import Image, ImageFile

    try:
        # Feeder JPEGs can be shorter than their declared height; Pillow only
        # decodes those with the (process-global) truncation flag set.
        with _TRUNCATED_LOCK:
            old = ImageFile.LOAD_TRUNCATED_IMAGES
            ImageFile.LOAD_TRUNCATED_IMAGES = True
            try:
                with Image.open(spool) as src:
                    img = src.convert("RGB")
            finally:
                ImageFile.LOAD_TRUNCATED_IMAGES = old
    except (OSError, ValueError, SyntaxError) as exc:
        raise bp.BrotherProtocolError(f"undecodable JPEG page: {exc}") from None
    if trim:
        img = _trim_fill_rows(img)
        if img.height == 0:
            raise bp.BrotherProtocolError("JPEG page contains no image rows")
    if want_height:
        img = _pad_white(img, want_height)
    img.save(out, format="TIFF", compression="tiff_adobe_deflate", dpi=dpi)
    return img.width, img.height


class BrotherScanClient:
    def __init__(
        self,
        host: str,
        port: int = bp.PORT,
        *,
        connect_timeout: float = 5.0,
        idle_timeout: float = 30.0,
        busy_retries: int = 32,
        busy_window: float = 90.0,
        retry_delay: float = 3.0,
        handshake_timeout: float = 10.0,
        probe_timeout: float = 3.0,
    ) -> None:
        self.host = host
        self.port = port
        self.connect_timeout = connect_timeout
        self.idle_timeout = idle_timeout
        self.busy_retries = max(1, busy_retries)
        self.busy_window = busy_window
        self.retry_delay = retry_delay
        self.handshake_timeout = handshake_timeout
        self.probe_timeout = probe_timeout

    # -- connection ---------------------------------------------------------

    async def _open(self):
        try:
            return await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), self.connect_timeout
            )
        except (OSError, asyncio.TimeoutError):
            raise ExternalServiceError("Could not connect to the scanner.") from None

    async def _read(
        self, reader: asyncio.StreamReader, n: int, timeout: float | None = None
    ) -> bytes:
        """Read up to n bytes (at least 1 unless EOF), bounded by ``timeout``
        (default: the idle timeout)."""
        try:
            return await asyncio.wait_for(reader.read(n), timeout or self.idle_timeout)
        except asyncio.TimeoutError:
            raise _ReadTimeoutError("The scanner stopped responding.") from None
        except OSError:
            raise ExternalServiceError("The connection to the scanner was lost.") from None

    async def _read_exact(
        self, reader: asyncio.StreamReader, n: int, timeout: float | None = None
    ) -> bytes | None:
        """Exactly n bytes; None on clean EOF before the first byte."""
        buf = b""
        while len(buf) < n:
            chunk = await self._read(reader, n - len(buf), timeout)
            if not chunk:
                if not buf:
                    return None
                raise bp.BrotherProtocolError(f"connection closed after {len(buf)} of {n} bytes")
            buf += chunk
        return buf

    @staticmethod
    async def _send(writer: asyncio.StreamWriter, data: bytes) -> None:
        try:
            writer.write(data)
            await writer.drain()
        except OSError:
            raise ExternalServiceError("The connection to the scanner was lost.") from None

    @staticmethod
    async def _close(writer: asyncio.StreamWriter | None) -> None:
        if writer is None:
            return
        try:
            writer.close()
            await asyncio.wait_for(writer.wait_closed(), 2)
        except Exception:  # best effort; the device tolerates a dropped session
            pass

    async def _read_lease(self, reader, writer, dpi: int, mode: str, timeout: float):
        """Greeting and lease; raises ``_BusyError`` for every busy signal,
        including a silent scanner."""
        try:
            greeting = await self._read_exact(reader, len(bp.GREETING_OK), timeout)
            if greeting is None or greeting == bp.GREETING_BUSY:
                raise _BusyError
            if greeting != bp.GREETING_OK:
                raise bp.BrotherProtocolError(f"unexpected greeting: {greeting!r}")
            await self._send(writer, bp.encode_lease_request(dpi, mode))
            prefix = await self._read_exact(reader, 3, timeout)
            if prefix is None or prefix[:2] == b"\x00\x00" or prefix[0] == bp.LEASE_NOT_READY:
                raise _BusyError
            body = b""
            if prefix[1]:
                body = await self._read_exact(reader, prefix[1], timeout) or b""
            return bp.parse_lease(prefix + body)
        except _ReadTimeoutError:
            raise _BusyError from None

    async def _handshake(self, dpi: int, mode: str):
        """Return (reader, writer, lease, feeder_has_paper).

        Retries while the scanner is busy or silent. The feeder query is part
        of the session: no caller sees a session before its reply was read.
        """
        deadline = time.monotonic() + self.busy_window
        for attempt in range(self.busy_retries):
            if attempt:
                if time.monotonic() + self.retry_delay >= deadline:
                    break
                await asyncio.sleep(self.retry_delay)
            reader, writer = await self._open()
            try:
                lease = await self._read_lease(reader, writer, dpi, mode, self.handshake_timeout)
                await self._send(writer, bp.CMD_FEEDER_QUERY)
                try:
                    reply = await self._read_exact(reader, 1, self.handshake_timeout)
                except _ReadTimeoutError:
                    raise _BusyError from None
                if reply is None or reply[0] not in (bp.FEEDER_LOADED, bp.FEEDER_EMPTY):
                    raise bp.BrotherProtocolError(f"unexpected feeder reply: {reply!r}")
                return reader, writer, lease, reply[0] == bp.FEEDER_LOADED
            except _BusyError:
                await self._close(writer)
            except BaseException:
                await self._close(writer)
                raise
        raise ScannerBusyError(_BUSY_DETAIL)

    async def probe(self) -> dict:
        """Connect and take a lease without scanning.

        Returns ``{"available": True, "busy": bool}``; a busy or silent
        scanner is reachable. Raises ``ExternalServiceError`` if it cannot be
        reached.
        """
        reader, writer = await self._open()
        try:
            await self._read_lease(reader, writer, 300, "TEXT", self.probe_timeout)
            return {"available": True, "busy": False}
        except _BusyError:
            return {"available": True, "busy": True}
        finally:
            await self._close(writer)

    async def feeder_reading(self, timeout: float = 3.0) -> FeederReading | None:
        """Read the feeder state: lease plus ``ESC D``, never ``ESC X``.

        None when the scanner is busy, silent, unreachable or slower than
        ``timeout`` overall.
        """
        async def read() -> FeederReading | None:
            reader, writer = await self._open()
            try:
                lease = await self._read_lease(reader, writer, 300, "TEXT", timeout)
                await self._send(writer, bp.CMD_FEEDER_QUERY)
                reply = await self._read_exact(reader, 1, timeout)
                if reply is None or reply[0] not in (bp.FEEDER_LOADED, bp.FEEDER_EMPTY):
                    return None
                return FeederReading(reply[0] == bp.FEEDER_LOADED, lease.paper_in_feeder)
            except (_BusyError, ExternalServiceError, bp.BrotherProtocolError):
                return None
            finally:
                await self._close(writer)

        try:
            return await asyncio.wait_for(read(), timeout)
        except (asyncio.TimeoutError, ExternalServiceError):
            return None

    # -- scan ---------------------------------------------------------------

    async def scan(
        self,
        *,
        mode: str,
        dpi: int,
        source: str,
        out_dir: str,
        area_mm: tuple[float, float, float, float] | None = None,
        progress: ProgressCallback | None = None,
    ) -> list[ScannedPage]:
        """Scan and return one TIFF per page, written into ``out_dir``.

        A feeder request always delivers every sheet; the device cannot be
        stopped after the first. The printer also feeds a glass request when
        either paper signal shows paper in the feeder, so that request is sent
        as a full-page feeder request (``area_mm`` dropped) and every page
        comes back marked ``from_feeder``.

        ``source`` is ``"flatbed"`` or ``"adf"``; ``area_mm`` is
        ``(left, top, width, height)`` and flatbed only (None = whole bed).
        """
        spec = bp.get_mode(mode)
        bp.check_resolution(dpi)
        if source not in ("flatbed", "adf"):
            raise ScanRequestError("Unknown scan source.")
        if source == "adf" and area_mm is not None:
            raise ScanRequestError("The document feeder always scans the full page.")

        created: list[str] = []
        writer = None
        stream = _Stream()
        try:
            reader, writer, lease, paper = await self._handshake(dpi, mode)

            if source == "adf" and not (paper and lease.paper_in_feeder):
                raise ScanSourceError(FEEDER_EMPTY_DETAIL)
            if source == "flatbed" and (paper or lease.paper_in_feeder):
                source = "adf"

            if source == "adf":
                area = bp.feeder_area(lease)
            elif area_mm is None:
                area = (0, 0, bp.aligned_width(lease.width_px), lease.height_px)
            else:
                area = bp.area_to_pixels(dpi, *area_mm, lease.width_px, lease.height_px)

            stream.feeder = source == "adf"
            stream.parser = bp.StreamParser(
                max_page_bytes=_MAX_PAGE_BYTES,
                max_rows=(area[3] - area[1]) + 16 if not spec.jpeg else 0,
            )
            stream.started = True
            await self._send(writer, bp.encode_start(lease.xdpi, lease.ydpi, mode, area))
            return await self._receive(
                reader, writer, spec, lease, area, source, out_dir, created, progress, stream
            )
        except BaseException as exc:
            for path in created:
                try:
                    os.unlink(path)
                except OSError:
                    pass
            if isinstance(exc, bp.BrotherProtocolError):
                logger.warning("Brother protocol error: %s", exc.log_message)
            if stream.should_drain(exc):
                await self._drain_shielded(reader, stream.parser)
            raise
        finally:
            await self._close(writer)

    async def _drain_shielded(self, reader, parser: bp.StreamParser) -> None:
        """Read an abandoned feeder stream to its end, however often the caller
        is cancelled meanwhile; a cancel is only re-raised once the drain is done."""
        task = asyncio.ensure_future(self._drain(reader, parser))
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break

    async def _drain(self, reader, parser: bp.StreamParser) -> None:
        """Discard data up to the job end. A silent, dropped or garbled stream, or
        a device error, ends it quietly; so does the overall deadline."""
        async def read_out() -> None:
            while not parser.finished:
                chunk = await self._read(reader, 65536)
                if not chunk:
                    return
                if any(isinstance(ev, bp.DeviceError) for ev in parser.feed(chunk)):
                    return

        try:
            await asyncio.wait_for(read_out(), _DRAIN_DEADLINE_SECONDS)
        except asyncio.TimeoutError:
            logger.warning("Gave up reading out the feeder stream after %ss",
                           _DRAIN_DEADLINE_SECONDS)
        except (ExternalServiceError, bp.BrotherProtocolError):
            return

    async def _receive(
        self, reader, writer, spec, lease, area, source, out_dir, created, progress, stream
    ):
        width = area[2] - area[0]
        want_height = area[3] - area[1]
        row_bytes = spec.row_bytes(width)
        flatbed = source == "flatbed"
        feeder = not flatbed
        parser = stream.parser
        dpi = (lease.xdpi, lease.ydpi)
        pages: list[ScannedPage] = []
        spool_path: str | None = None
        spool = None
        rows = 0
        last_pct = -1
        current = 0

        async def report(page: int, pct: int) -> None:
            if progress is not None:
                await progress(page, pct)

        try:
            while not parser.finished:
                chunk = await self._read(reader, 65536)
                if not chunk:
                    raise ExternalServiceError("The scanner closed the connection unexpectedly.")
                try:
                    events = parser.feed(chunk)
                except bp.BrotherProtocolError:
                    stream.garbled = True
                    raise
                for ev in events:
                    if isinstance(ev, bp.Frame):
                        if spool is None:
                            current = len(pages) + 1
                            spool_path = os.path.join(out_dir, f"page-{current:03d}.spool")
                            created.append(spool_path)
                            spool = open(spool_path, "wb")
                            rows, last_pct = 0, -1
                            await report(current, 0)
                        if spec.jpeg:
                            spool.write(ev.payload)
                        else:
                            if ev.kind == bp.KIND_PACKBITS_ROW:
                                row = bp.packbits_decode(ev.payload, row_bytes)
                            else:
                                row = ev.payload
                            if len(row) != row_bytes:
                                raise bp.BrotherProtocolError(
                                    f"row of {len(row)} bytes, expected {row_bytes}"
                                )
                            spool.write(row.translate(_INVERT) if spec.invert else row)
                            rows += 1
                            pct = min(99, rows * 100 // max(1, want_height))
                            if pct != last_pct:
                                last_pct = pct
                                await report(current, pct)
                    elif isinstance(ev, bp.PageEnd):
                        if spool is None:
                            raise bp.BrotherProtocolError("page end without data")
                        spool.close()
                        spool = None
                        out = os.path.join(out_dir, f"page-{current:03d}.tiff")
                        created.append(out)
                        if spec.jpeg:
                            w, h = await asyncio.to_thread(
                                _finish_jpeg_page, spool_path, out, not flatbed,
                                want_height if flatbed else None, dpi,
                            )
                        else:
                            if rows == 0:
                                raise bp.BrotherProtocolError("page with no rows")
                            w, h = await asyncio.to_thread(
                                _finish_raw_page, spool_path, out, spec, width, rows,
                                want_height if flatbed else None, dpi,
                            )
                        os.unlink(spool_path)
                        created.remove(spool_path)
                        pages.append(ScannedPage(out, w, h, lease.xdpi, spec.pil_mode, feeder))
                        await report(current, 100)
                    elif isinstance(ev, bp.DeviceError):
                        logger.warning("Brother scanner reported error status 0x%02x", ev.code)
                        await self._reset(writer)
                        raise ExternalServiceError(
                            "The scanner reported an error (possibly a paper jam). "
                            "Check the scanner and try again."
                        )
            if spool is not None or not pages:
                raise bp.BrotherProtocolError("job ended without a complete page")
            return pages
        finally:
            if spool is not None:
                spool.close()
            if spool_path and os.path.exists(spool_path):
                os.unlink(spool_path)

    @staticmethod
    async def _reset(writer: asyncio.StreamWriter) -> None:
        try:
            writer.write(bp.CMD_RESET)
            await asyncio.wait_for(writer.drain(), 2)
        except Exception:
            pass


class _Stream:
    """What the drain needs to know about the page stream."""

    def __init__(self) -> None:
        self.parser = bp.StreamParser()
        self.feeder = False
        self.started = False  # ESC X is on its way
        self.garbled = False

    def should_drain(self, exc: BaseException) -> bool:
        """Only a feeder stream still running and readable is worth reading out.
        An ExternalServiceError means the stream is already over or unreadable,
        except a BrotherProtocolError, which can come from our own page handling
        with the stream still in sync (``garbled`` marks the parser's own)."""
        return (
            self.feeder and self.started and not self.garbled
            and not self.parser.finished
            and not (
                isinstance(exc, ExternalServiceError)
                and not isinstance(exc, bp.BrotherProtocolError)
            )
        )


class _ReadTimeoutError(ExternalServiceError):
    """Internal: a read timed out."""


class _BusyError(Exception):
    """Internal: scanner busy (-NG 401 or empty lease); retry."""


__all__ = ["BrotherScanClient", "FeederReading", "ScannedPage"]
