"""asyncio client for Brother network scanners (see ``brother_protocol``).

One ``scan()`` call is one connection: handshake (with bounded retries while
the device is busy), safety checks on the feeder, then the page stream, each
page spooled to disk and converted to a lossless TIFF. Host validation is the
caller's job. A feeder request always pulls every sheet; hanging up does not
stop it.
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


@dataclass(frozen=True)
class ScannedPage:
    path: str
    width: int
    height: int
    dpi: int
    mode: str  # PIL mode of the TIFF


def _invert_table() -> bytes:
    return bytes(255 - i for i in range(256))


_INVERT = _invert_table()


def _trim_fill_rows(img):
    """Drop trailing rows that are exactly the decoder's fill colour."""
    import numpy as np

    arr = np.asarray(img)
    filled = (arr == _FILL_VALUE).all(axis=(1, 2))
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
        stopped after the first.

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
        try:
            reader, writer, lease, paper = await self._handshake(dpi, mode)

            if source == "flatbed" and (paper or lease.paper_in_feeder):
                raise ScanSourceError(
                    "There is paper in the document feeder. "
                    "Remove it before scanning from the glass."
                )
            if source == "adf" and not (paper and lease.paper_in_feeder):
                raise ScanSourceError(
                    "The document feeder is empty. "
                    "Load paper in the feeder, or scan from the glass."
                )

            if source == "adf":
                area = bp.feeder_area(lease)
            elif area_mm is None:
                area = (0, 0, lease.width_px, lease.height_px)
            else:
                area = bp.area_to_pixels(dpi, *area_mm, lease.width_px, lease.height_px)

            await self._send(writer, bp.encode_start(lease.xdpi, lease.ydpi, mode, area))
            return await self._receive(
                reader, writer, spec, lease, area, source, out_dir, created, progress
            )
        except BaseException as exc:
            for path in created:
                try:
                    os.unlink(path)
                except OSError:
                    pass
            if isinstance(exc, bp.BrotherProtocolError):
                logger.warning("Brother protocol error: %s", exc.log_message)
            raise
        finally:
            await self._close(writer)

    async def _receive(self, reader, writer, spec, lease, area, source, out_dir, created, progress):
        width = area[2] - area[0]
        want_height = area[3] - area[1]
        row_bytes = spec.row_bytes(width)
        flatbed = source == "flatbed"
        parser = bp.StreamParser(
            max_page_bytes=_MAX_PAGE_BYTES,
            max_rows=want_height + 16 if not spec.jpeg else 0,
        )
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
                for ev in parser.feed(chunk):
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
                        pages.append(ScannedPage(out, w, h, lease.xdpi, spec.pil_mode))
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


class _ReadTimeoutError(ExternalServiceError):
    """Internal: a read timed out."""


class _BusyError(Exception):
    """Internal: scanner busy (-NG 401 or empty lease); retry."""


__all__ = ["BrotherScanClient", "ScannedPage"]
