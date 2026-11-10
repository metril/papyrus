import asyncio
import logging
import os
import re
import shutil
import time
import uuid
from typing import Awaitable, Callable, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import PapyrusError, ScannerBusyError, ScanRequestError
from app.services import brother_protocol as bp
from app.services.brother_client import BrotherScanClient, FeederReading
from app.services.net_guard import UnsafeHostError, assert_safe_host

logger = logging.getLogger(__name__)

_DEFAULT_SCAN_DIR = "/app/data/scans"

# F41: scanimage -L (device listing) is expected to return almost instantly;
# a full scan can legitimately take a while at high resolution.
_LIST_TIMEOUT_SECONDS = 20
_SCAN_TIMEOUT_SECONDS = 300
# A multi-page ADF batch can legitimately run far longer than a single scan
# (many pages at high resolution); the PDF merge step is CPU-bound and
# shouldn't need anywhere near as long.
_BATCH_SCAN_TIMEOUT_SECONDS = 900
# brother4 batches may legitimately run for many minutes while paper moves;
# only a stall (no stderr line, no new page file) for this long is a hang.
_BATCH_NO_PROGRESS_SECONDS = 120
_BATCH_POLL_SECONDS = 1.0
# Lower-case text scanimage prints when the feeder simply ran empty.
SANE_END_OF_FEED_TEXT = "out of documents"
_PDF_MERGE_TIMEOUT_SECONDS = 300


class ScanError(PapyrusError):
    status_code = 502


def _scanimage_match_candidates(device: str) -> list[str]:
    """Return substrings to search `scanimage -L`'s output for, given a
    stored `Scanner.device` string (F42 fix-up).

    Most device strings (`brother4:...`, plain SANE device paths) appear in
    `scanimage -L`'s output verbatim, between backticks -- register_brscan4
    parses them out with exactly that assumption. `airscan:` devices are the
    exception: `probe_scanner_ip`'s manual eSCL-port-probing fallback stores
    them as `airscan:{prefix}:{label}:{url}` (the URL is needed later to
    reconstruct airscan.conf), but `_write_airscan_device` registers the
    device in sane-airscan under just its label, so `scanimage -L` actually
    prints `airscan:{prefix}:{label}` with no URL suffix at all -- matching
    on the full stored string alone made a perfectly working eSCL scanner
    report unavailable. `maxsplit=3` stops after the third colon so a colon
    inside the URL itself (`http://...`) doesn't fragment it.
    """
    candidates = [device]
    if device.startswith("airscan:"):
        parts = device.split(":", 3)
        if len(parts) >= 3:
            candidates.append(":".join(parts[:3]))
    return candidates


def _driver_options(device: str, mode: str, source: str) -> tuple[str, str]:
    """Translate Papyrus's mode and source names into what `device`'s driver
    accepts. brscan4 rejects the generic names outright (scanimage: "setting
    of option --mode failed"); other drivers take them as they are."""
    if not device.startswith("brother4:"):
        return mode, source
    return (
        {"Color": "24bit Color", "Gray": "True Gray", "Lineart": "Black & White"}.get(mode, mode),
        {
            "flatbed": "FlatBed",
            "adf": "Automatic Document Feeder(left aligned)",
        }.get(source.lower(), source),
    )


async def _await_with_timeout(coro, process: asyncio.subprocess.Process,
                               timeout: float, timeout_message: str):
    """Await `coro` (which itself awaits/reads from `process`), bounded
    by `timeout` and cancellation-safe (F41/F110): on a timeout OR the
    calling task being cancelled (e.g. an eSCL client cancelling the
    job, or a future caller of scan_batch), `process` is killed and
    reaped before the exception propagates, instead of being left
    running unreaped -- a hung subprocess otherwise holds `self._lock`
    forever, wedging every subsequent scan (web UI and eSCL alike) with
    "Scanner is busy" until the process restarts.
    """
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise ScanError(timeout_message)
    except asyncio.CancelledError:
        process.kill()
        await process.wait()
        raise


class ScanBackend(Protocol):
    """A way of talking to a scanner. `ScanService` owns locking, format
    conversion, merging and cleanup; a backend only acquires raw page images
    and reports on the device."""

    async def acquire_page(
        self,
        *,
        device: str,
        resolution: int,
        mode: str,
        source: str,
        tiff_file: str,
        scan_id: str,
        progress_callback: Callable[[str, float], Awaitable[None]] | None = None,
        left_mm: float | None = None,
        top_mm: float | None = None,
        width_mm: float | None = None,
        height_mm: float | None = None,
    ) -> list[str] | None:
        """Scan one page and write it as a TIFF to `tiff_file`. A backend whose
        device feeds a glass request instead returns the page paths (in a
        directory the caller owns) and leaves `tiff_file` unwritten."""
        ...

    async def acquire_batch(
        self,
        *,
        device: str,
        resolution: int,
        mode: str,
        page_dir: str,
        scan_id: str,
        progress_callback: Callable[[str, float], Awaitable[None]] | None = None,
        glass_request: bool = False,
    ) -> bool | None:
        """Scan an ADF batch, writing page TIFFs (``*.tiff``) into `page_dir`.
        `glass_request` is a glass scan the device may serve from the feeder; the
        result says whether the feeder served it (None: not reported)."""
        ...

    async def probe(self, device: str) -> dict:
        """Report whether `device` is reachable (see `ScanService.check_device`)."""
        ...

    async def options(self, device: str) -> dict:
        """Supported resolutions/modes/formats/sources for `device`."""
        ...


class SaneBackend:
    """`scanimage` (SANE) subprocess backend."""

    async def probe(self, device: str) -> dict:
        process = await asyncio.create_subprocess_exec(
            "scanimage", "-L",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=_LIST_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise ScannerBusyError("Timed out listing scanner devices")

        output = stdout.decode() + stderr.decode()
        candidates = _scanimage_match_candidates(device) if device else []

        return {
            "available": any(c in output for c in candidates),
            "device": device,
            "output": output.strip(),
        }

    async def options(self, device: str) -> dict:
        return {
            "resolutions": (
                [100, 150, 200, 300, 400, 600] if device.startswith("brother4:")
                else [75, 100, 150, 200, 300, 600]
            ),
            "modes": ["Color", "Gray", "Lineart"],
            "formats": ["png", "jpeg", "tiff", "pdf"],
            "feeder_formats": (
                ["pdf"] if device.startswith("brother4:") else ["png", "jpeg", "tiff", "pdf"]
            ),
            "sources": ["Flatbed", "ADF"],
        }

    async def acquire_page(
        self,
        *,
        device: str,
        resolution: int,
        mode: str,
        source: str,
        tiff_file: str,
        scan_id: str,
        progress_callback: Callable[[str, float], Awaitable[None]] | None = None,
        left_mm: float | None = None,
        top_mm: float | None = None,
        width_mm: float | None = None,
        height_mm: float | None = None,
    ) -> None:
        _mode, _source = _driver_options(device, mode, source)

        cmd = [
            "scanimage",
            "-d", device,
            "--resolution", str(resolution),
            "--mode", _mode,
            "--format=tiff",
            "--source", _source,
            "--progress",
            "-o", tiff_file,
        ]

        # Scan geometry: restrict to requested area (all values in mm)
        if left_mm is not None:
            cmd += ["-l", str(round(left_mm, 2))]
        if top_mm is not None:
            cmd += ["-t", str(round(top_mm, 2))]
        if width_mm is not None:
            cmd += ["-x", str(round(width_mm, 2))]
        if height_mm is not None:
            cmd += ["-y", str(round(height_mm, 2))]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stderr_lines = await self._read_scan_output(process, scan_id, progress_callback)

        if process.returncode != 0:
            logger.warning(
                "scanimage exited %s: %s",
                process.returncode, "; ".join(ln for ln in stderr_lines if ln),
            )
            raise ScanError("The scan failed. Check the scanner and try again.")

    async def _read_scan_output(
        self,
        process: asyncio.subprocess.Process,
        scan_id: str,
        progress_callback: Callable[[str, float], Awaitable[None]] | None,
    ) -> list[str]:
        """Read `process`'s stderr for progress lines and wait for exit,
        bounded by `_SCAN_TIMEOUT_SECONDS` (F41) and cancellation-safe (F110)
        via `_await_with_timeout`.
        """

        async def _read() -> list[str]:
            stderr_lines: list[str] = []
            if process.stderr:
                async for line in process.stderr:
                    text = line.decode().strip()
                    stderr_lines.append(text)
                    match = re.search(r"Progress: (\d+\.?\d*)%", text)
                    if match and progress_callback:
                        await progress_callback(scan_id, float(match.group(1)))
            await process.wait()
            return stderr_lines

        return await _await_with_timeout(
            _read(), process, _SCAN_TIMEOUT_SECONDS, "Scan timed out"
        )

    async def acquire_batch(
        self,
        *,
        device: str,
        resolution: int,
        mode: str,
        page_dir: str,
        scan_id: str,
        progress_callback: Callable[[str, float], Awaitable[None]] | None = None,
        glass_request: bool = False,
    ) -> None:
        _mode, _source = _driver_options(device, mode, "ADF")

        # scanimage --batch mode scans all pages from ADF
        cmd = [
            "scanimage",
            "-d", device,
            "--resolution", str(resolution),
            "--mode", _mode,
            "--format=tiff",
            "--source", _source,
            # --batch takes its optional value only in the = form.
            f"--batch={os.path.join(page_dir, 'page_%04d.tiff')}",
            "--progress",
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stderr_lines: list[str] = []
        last_activity = [time.monotonic()]

        async def _read_batch() -> None:
            if process.stderr:
                async for line in process.stderr:
                    last_activity[0] = time.monotonic()
                    text = line.decode().strip()
                    match = re.search(r"Progress: (\d+\.?\d*)%", text)
                    if match:
                        if progress_callback:
                            await progress_callback(scan_id, float(match.group(1)))
                    elif text:
                        stderr_lines.append(text)
            await process.wait()

        if device.startswith("brother4:"):
            await _run_with_progress_watchdog(
                _read_batch(), process, page_dir, last_activity, "Batch scan timed out"
            )
        else:
            await _await_with_timeout(
                _read_batch(), process, _BATCH_SCAN_TIMEOUT_SECONDS, "Batch scan timed out"
            )

        if process.returncode != 0:
            tail = "; ".join(stderr_lines[-5:])
            if device.startswith("brother4:") and not any(
                SANE_END_OF_FEED_TEXT in ln.lower() for ln in stderr_lines
            ):
                logger.warning("scanimage batch exited %s: %s", process.returncode, tail)
                raise ScanError("The scan failed. Check the scanner and try again.")
            # A non-zero exit is normal once the feeder runs out, so it can't fail
            # the batch here, but with no pages it's the only clue to what went wrong.
            if not any(f.endswith(".tiff") for f in os.listdir(page_dir)):
                logger.warning(
                    "scanimage batch exited %s with no pages: %s", process.returncode, tail
                )


async def _run_with_progress_watchdog(
    coro, process, page_dir: str, last_activity: list[float], timeout_message: str
):
    """Run `coro`, killing `process` if nothing (stderr line or new page file)
    happens for `_BATCH_NO_PROGRESS_SECONDS`. Cancellation also kills it."""

    def page_count() -> int:
        try:
            return sum(1 for f in os.listdir(page_dir) if f.endswith(".tiff"))
        except OSError:
            return 0

    stalled = False
    reader = asyncio.ensure_future(coro)

    async def watchdog() -> None:
        nonlocal stalled
        seen = page_count()
        while True:
            await asyncio.sleep(_BATCH_POLL_SECONDS)
            now_pages = page_count()
            if now_pages > seen:
                seen = now_pages
                last_activity[0] = time.monotonic()
            if time.monotonic() - last_activity[0] > _BATCH_NO_PROGRESS_SECONDS:
                stalled = True
                process.kill()
                reader.cancel()
                return

    dog = asyncio.ensure_future(watchdog())
    try:
        return await reader
    except asyncio.CancelledError:
        if not stalled:
            reader.cancel()
            process.kill()
        await process.wait()
        if stalled:
            raise ScanError(timeout_message) from None
        raise
    finally:
        dog.cancel()


BROTHERNET_PREFIX = "brothernet:"
FEEDER_PDF_ONLY_DETAIL = (
    "Feeder scans on this scanner take every sheet at once, so they can only be "
    "saved as PDF. Choose PDF or scan from the glass."
)
_NATIVE_MODES = {"Color": "CGRAY", "Gray": "GRAY64", "Lineart": "TEXT"}
_HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")


def is_native_device(device: str) -> bool:
    return device.startswith(BROTHERNET_PREFIX)


def feeder_takes_every_sheet(device: str) -> bool:
    """Devices whose single feeder request always pulls the whole stack."""
    return is_native_device(device) or device.startswith("brother4:")


def parse_native_device(device: str) -> str:
    """Return the host of a `brothernet:<host>` device string."""
    host = device[len(BROTHERNET_PREFIX):] if is_native_device(device) else ""
    if not _HOST_RE.match(host):
        raise ScanRequestError("The scanner address is not valid.")
    return host


class BrotherNetBackend:
    """Native Brother network protocol backend (`brothernet:<host>` devices)."""

    def __init__(self, port: int = bp.PORT, **client_kwargs) -> None:
        self._port = port
        self._client_kwargs = client_kwargs

    def _client(self, device: str) -> BrotherScanClient:
        return BrotherScanClient(parse_native_device(device), self._port, **self._client_kwargs)

    @staticmethod
    def _progress(scan_id: str, callback):
        if callback is None:
            return None

        async def adapt(_page: int, percent: int) -> None:
            await callback(scan_id, float(percent))

        return adapt

    @staticmethod
    def _protocol_mode(mode: str) -> str:
        try:
            return _NATIVE_MODES[mode]
        except KeyError:
            raise ScanRequestError("Unsupported scan mode.") from None

    async def probe(self, device: str) -> dict:
        try:
            result = await self._client(device).probe()
        except ScanRequestError:
            raise
        except PapyrusError:
            return {"available": False, "busy": False, "device": device, "output": ""}
        return {"available": result["available"], "busy": result["busy"],
                "device": device, "output": ""}

    async def feeder_reading(self, device: str, timeout: float) -> FeederReading | None:
        return await self._client(device).feeder_reading(timeout)

    async def options(self, device: str) -> dict:
        return {
            "resolutions": list(bp.RESOLUTIONS),
            "modes": ["Color", "Gray", "Lineart"],
            "formats": ["png", "jpeg", "tiff", "pdf"],
            "feeder_formats": ["pdf"],
            "sources": ["Flatbed", "ADF"],
        }

    async def acquire_page(
        self,
        *,
        device: str,
        resolution: int,
        mode: str,
        source: str,
        tiff_file: str,
        scan_id: str,
        progress_callback: Callable[[str, float], Awaitable[None]] | None = None,
        left_mm: float | None = None,
        top_mm: float | None = None,
        width_mm: float | None = None,
        height_mm: float | None = None,
    ) -> list[str] | None:
        """Scan the glass. If the printer feeds instead (paper in the feeder), the
        request goes out as a full-page feeder request and the page paths are
        returned, in a ``batch_<scan_id>`` directory the caller owns; otherwise the
        single page is at ``tiff_file`` and None is returned."""
        client = self._client(device)
        if source.lower() == "adf":
            raise ScanRequestError(FEEDER_PDF_ONLY_DETAIL)
        area = None
        if width_mm and height_mm:
            area = (left_mm or 0.0, top_mm or 0.0, width_mm, height_mm)
        work = os.path.join(os.path.dirname(tiff_file), f"batch_{scan_id}")
        os.makedirs(work, exist_ok=True)
        keep = False
        try:
            pages = await client.scan(
                mode=self._protocol_mode(mode),
                dpi=resolution,
                source="flatbed",
                out_dir=work,
                area_mm=area,
                progress=self._progress(scan_id, progress_callback),
            )
            if pages[0].from_feeder:
                keep = True
                return [p.path for p in pages]
            os.replace(pages[0].path, tiff_file)
            return None
        finally:
            if not keep:
                shutil.rmtree(work, ignore_errors=True)

    async def acquire_batch(
        self,
        *,
        device: str,
        resolution: int,
        mode: str,
        page_dir: str,
        scan_id: str,
        progress_callback: Callable[[str, float], Awaitable[None]] | None = None,
        glass_request: bool = False,
    ) -> bool:
        """Scan the feeder into ``page_dir``; True if the printer fed it.

        ``glass_request`` sends the request the way a full glass scan goes, so
        paper in the feeder takes over but an emptied feeder falls back to the
        glass instead of failing.
        """
        client = self._client(device)
        pages = await client.scan(
            mode=self._protocol_mode(mode),
            dpi=resolution,
            source="flatbed" if glass_request else "adf",
            out_dir=page_dir,
            progress=self._progress(scan_id, progress_callback),
        )
        return pages[0].from_feeder


_LEFTOVER_DIR_RE = re.compile(
    r"^(?:(?:pages|batch)_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"|native_[A-Za-z0-9_]{8})$"
)


def remove_scan_leftovers(scan_dir: str) -> int:
    """Delete transient per-scan directories a previous run left behind.

    Only real directories whose names match the exact patterns this module
    creates are touched. Returns how many were removed.
    """
    removed = 0
    try:
        names = os.listdir(scan_dir)
    except OSError:
        return 0
    for name in names:
        path = os.path.join(scan_dir, name)
        if _LEFTOVER_DIR_RE.match(name) and os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
    return removed


async def _merge_pages_to_pdf(page_paths: list[str], pdf_file: str) -> None:
    pdf_process = await asyncio.create_subprocess_exec(
        "img2pdf", *page_paths, "-o", pdf_file,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await _await_with_timeout(
        pdf_process.wait(), pdf_process,
        _PDF_MERGE_TIMEOUT_SECONDS, "PDF merge timed out",
    )

    if pdf_process.returncode != 0:
        raise ScanError("Failed to merge pages into PDF")


_sane_backend = SaneBackend()
_brothernet_backend = BrotherNetBackend()


class ScanService:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._scan_dir = _DEFAULT_SCAN_DIR
        self._scanner_device = ""
        self._filename_template = "scan_{date}_{time}_{id}"

    def configure(self, scan_dir: str, scanner_device: str, filename_template: str) -> None:
        """Set the module singleton's fallback defaults.

        F43: this is no longer called per-request -- routers/eSCL/copy each
        resolve scan_dir/device/filename_template fresh from the DB and pass
        them straight into scan()/scan_batch()/run_post_scan_actions instead,
        so a concurrent settings change (or a second in-flight scan) can't
        mutate another request's in-progress config out from under it. Kept
        for the legacy settings-configured-device fallback in
        get_default_scanner_device() below.
        """
        self._scan_dir = scan_dir or _DEFAULT_SCAN_DIR
        self._scanner_device = scanner_device or ""
        self._filename_template = filename_template or "scan_{date}_{time}_{id}"

    def is_busy(self) -> bool:
        """Whether a scan is currently in progress (the scan lock is held)."""
        return self._lock.locked()

    async def check_device(self, device: str | None = None) -> dict:
        """Check if the scanner device is available.

        Availability is derived solely from the configured device string (or
        its airscan label, see `_scanimage_match_candidates`) being present
        in `scanimage -L`'s output (F42) -- `scanimage -L` exits 0 even when
        it finds no scanners at all, so a bare `returncode == 0` check used
        to report "available" for every device, including an
        empty/unconfigured one.
        """
        _device = device if device is not None else self._scanner_device
        await self._guard(_device)
        return await self._backend_for(_device).probe(_device)

    async def native_feeder_reading(
        self, device: str, timeout: float = 3.0
    ) -> FeederReading | None:
        """Feeder state of a `brothernet:` device without scanning; None if it
        isn't one, can't be read, or is busy."""
        if not is_native_device(device):
            return None
        try:
            await self._guard(device)
            return await _brothernet_backend.feeder_reading(device, timeout)
        except PapyrusError:
            return None

    async def get_options(self, device: str | None = None) -> dict:
        """Get available scanner options for `device` (default: the configured one)."""
        _device = device if device is not None else self._scanner_device
        return await self._backend_for(_device).options(_device)

    def _backend_for(self, device: str) -> ScanBackend:
        """Pick the backend that drives `device`."""
        return _brothernet_backend if is_native_device(device) else _sane_backend

    async def _guard(self, device: str) -> None:
        """Validate a native device's address before any connection to it."""
        if not is_native_device(device):
            return
        host = parse_native_device(device)
        try:
            await assert_safe_host(host)
        except UnsafeHostError:
            raise ScanRequestError("The scanner address is not allowed.") from None

    # ------------------------------------------------------------------
    # Synchronous bodies (run inside a worker thread).
    # ------------------------------------------------------------------

    def _convert_scan_sync(
        self, tiff_file: str, out_file: str, fmt: str, resolution: int
    ) -> None:
        """Convert a scanned TIFF to the requested output format using Pillow.

        (Pillow handles JPEG-in-TIFF from airscan; img2pdf rejects lossy TIFF).
        CPU-bound decode/encode — run via ``asyncio.to_thread`` so it doesn't
        block the event loop while the caller still holds the scan lock.
        """
        from PIL import Image

        with Image.open(tiff_file) as img:
            if fmt == "jpeg":
                # img.copy() forces full pixel decode so Pillow re-encodes
                # the JPEG from scratch — without it, Pillow may copy the raw
                # JPEG bytes from a JPEG-in-TIFF without setting DPI metadata.
                img = img.convert("RGB") if img.mode not in ("RGB", "L") else img.copy()
                img.save(out_file, format="JPEG", dpi=(resolution, resolution), quality=95)
            elif fmt == "png":
                if img.mode not in ("RGB", "L", "RGBA"):
                    img = img.convert("RGB")
                img.save(out_file, format="PNG", dpi=(resolution, resolution))
            else:  # pdf
                if img.mode not in ("RGB", "L", "RGBA"):
                    img = img.convert("RGB")
                img.save(out_file, format="PDF", resolution=resolution)

    # ------------------------------------------------------------------
    # Async public API.
    # ------------------------------------------------------------------

    async def scan(
        self,
        resolution: int = 300,
        mode: str = "Color",
        fmt: str = "pdf",
        source: str = "Flatbed",
        progress_callback: Callable[[str, float], Awaitable[None]] | None = None,
        device: str | None = None,
        scan_dir: str | None = None,
        filename_template: str | None = None,
        left_mm: float | None = None,
        top_mm: float | None = None,
        width_mm: float | None = None,
        height_mm: float | None = None,
        page_count_callback: Callable[[int], None] | None = None,
        feeder_callback: Callable[[], None] | None = None,
    ) -> tuple[str, str]:
        """Perform a scan.

        Args:
            resolution: DPI (75-600)
            mode: Color, Gray, or Lineart
            fmt: Output format (png, jpeg, tiff, pdf)
            source: Flatbed or ADF
            progress_callback: Async callback(scan_id, percent)
            device: SANE device string (F43: per-call, snapshotted by the
                caller from the DB rather than read off the shared singleton).
            scan_dir: Output directory (F43: same rationale as `device`).
            filename_template: accepted for interface symmetry with
                `device`/`scan_dir` (F43's per-request settings snapshot) —
                not consumed here, since the intermediate/output files are
                always named from `scan_id`; callers use it for the
                delivered filename (see `run_post_scan_actions`).

        Returns:
            Tuple of (scan_id, output_filepath)

        A native Brother or brother4 feeder request delivers every sheet, so it is
        PDF-only and `page_count_callback` (if given) receives the sheet count.

        On a `brothernet:` device paper in the feeder takes over a glass request
        too: it is scanned as a full-page feeder request, every sheet is kept and
        the result is one PDF whatever `fmt` asked for. `feeder_callback` is called
        when that happened to a glass request.
        """
        native_feeder = feeder_takes_every_sheet(device or self._scanner_device) and (
            source.lower() == "adf"
        )
        if native_feeder and fmt != "pdf":
            raise ScanRequestError(FEEDER_PDF_ONLY_DETAIL)
        if self._lock.locked():
            raise ScanError("Scanner is busy. Try again later.")

        async with self._lock:
            scan_id = str(uuid.uuid4())
            _scan_dir = scan_dir or self._scan_dir
            _device = device or self._scanner_device

            # Scan to TIFF as intermediate format, then convert to requested output
            tiff_file = os.path.join(_scan_dir, f"{scan_id}.tiff")

            glass_over_feeder = False
            if is_native_device(_device) and not native_feeder:
                reading = await self.native_feeder_reading(_device)
                glass_over_feeder = reading is not None and reading.takes_over_glass

            if native_feeder or glass_over_feeder:
                pdf_file, count, fed = await self._scan_feeder_pdf(
                    device=_device,
                    resolution=resolution,
                    mode=mode,
                    scan_dir=_scan_dir,
                    scan_id=scan_id,
                    progress_callback=progress_callback,
                    glass_request=glass_over_feeder,
                    glass_fmt=fmt,
                )
                if page_count_callback is not None:
                    page_count_callback(count)
                if fed and glass_over_feeder and feeder_callback is not None:
                    feeder_callback()
                return scan_id, pdf_file

            keep_tiff = False
            try:
                await self._guard(_device)
                fed_pages = await self._backend_for(_device).acquire_page(
                    device=_device,
                    resolution=resolution,
                    mode=mode,
                    source=source,
                    tiff_file=tiff_file,
                    scan_id=scan_id,
                    progress_callback=progress_callback,
                    left_mm=left_mm,
                    top_mm=top_mm,
                    width_mm=width_mm,
                    height_mm=height_mm,
                )

                if fed_pages:
                    pdf_file = os.path.join(_scan_dir, f"{scan_id}.pdf")
                    try:
                        await _merge_pages_to_pdf(fed_pages, pdf_file)
                    except BaseException:
                        if os.path.exists(pdf_file):
                            os.unlink(pdf_file)
                        raise
                    finally:
                        shutil.rmtree(os.path.dirname(fed_pages[0]), ignore_errors=True)
                    if page_count_callback is not None:
                        page_count_callback(len(fed_pages))
                    if feeder_callback is not None:
                        feeder_callback()
                    return scan_id, pdf_file

                if not os.path.exists(tiff_file):
                    raise ScanError("Scan produced no output file")

                # Convert TIFF to the requested format using Pillow
                # (Pillow handles JPEG-in-TIFF from airscan; img2pdf rejects lossy TIFF)
                if fmt in ("pdf", "png", "jpeg"):
                    ext = {"jpeg": "jpg"}.get(fmt, fmt)  # jpeg→jpg, pdf→pdf, png→png
                    out_file = os.path.join(_scan_dir, f"{scan_id}.{ext}")
                    await asyncio.to_thread(
                        self._convert_scan_sync, tiff_file, out_file, fmt, resolution
                    )
                    os.unlink(tiff_file)
                    return scan_id, out_file
                else:
                    # tiff — return as-is
                    keep_tiff = True
                    return scan_id, tiff_file
            finally:
                # F22: any non-success exit (scanimage failure, conversion
                # failure, or a cancelled/timed-out scan) leaves the
                # intermediate TIFF behind unless we clean it up here --
                # nothing else references it, so retention could never
                # reclaim it either.
                if not keep_tiff and os.path.exists(tiff_file):
                    os.unlink(tiff_file)

    async def _scan_feeder_pdf(
        self,
        *,
        device: str,
        resolution: int,
        mode: str,
        scan_dir: str,
        scan_id: str,
        progress_callback: Callable[[str, float], Awaitable[None]] | None,
        glass_request: bool = False,
        glass_fmt: str = "pdf",
    ) -> tuple[str, int, bool]:
        """Scan every sheet in the feeder into one PDF. Caller holds the lock.

        Returns (path, page_count, fed_by_feeder). A glass request the printer
        served from the glass after all (paper lifted since the pre-check) comes
        back as the one scanned page in `glass_fmt`, not as a PDF.
        """
        page_dir = os.path.join(scan_dir, f"batch_{scan_id}")
        pdf_file = os.path.join(scan_dir, f"{scan_id}.pdf")
        os.makedirs(page_dir, exist_ok=True)

        success = False
        try:
            pages, fed = await self._acquire_feeder_tiffs(
                device=device, resolution=resolution, mode=mode, page_dir=page_dir,
                scan_id=scan_id, progress_callback=progress_callback,
                glass_request=glass_request,
            )
            if glass_request and not fed and glass_fmt != "pdf":
                ext = {"jpeg": "jpg"}.get(glass_fmt, glass_fmt)
                out_file = os.path.join(scan_dir, f"{scan_id}.{ext}")
                if glass_fmt == "tiff":
                    os.replace(pages[0], out_file)
                else:
                    await asyncio.to_thread(
                        self._convert_scan_sync, pages[0], out_file, glass_fmt, resolution
                    )
                shutil.rmtree(page_dir, ignore_errors=True)
                success = True
                return out_file, 1, False

            await _merge_pages_to_pdf(pages, pdf_file)

            # Clean up individual page files
            shutil.rmtree(page_dir, ignore_errors=True)

            success = True
            return pdf_file, len(pages), fed
        finally:
            # F22: any non-success exit leaks page_dir and every page
            # TIFF in it (up to hundreds of MB for a large ADF batch)
            # unless cleaned up here -- nothing references page_dir from
            # the DB, so retention can never reclaim it either.
            if not success:
                shutil.rmtree(page_dir, ignore_errors=True)
                for leftover in (pdf_file, *(
                    os.path.join(scan_dir, f"{scan_id}.{e}") for e in ("jpg", "png", "tiff")
                )):
                    if os.path.exists(leftover):
                        os.unlink(leftover)

    async def _acquire_feeder_tiffs(
        self, *, device: str, resolution: int, mode: str, page_dir: str, scan_id: str,
        progress_callback: Callable[[str, float], Awaitable[None]] | None,
        glass_request: bool = False,
    ) -> tuple[list[str], bool]:
        await self._guard(device)
        fed = await self._backend_for(device).acquire_batch(
            device=device,
            resolution=resolution,
            mode=mode,
            page_dir=page_dir,
            scan_id=scan_id,
            progress_callback=progress_callback,
            glass_request=glass_request,
        )

        # scanimage returns non-zero when ADF runs out of paper, which is expected
        # Check if we got any pages
        pages = sorted(f for f in os.listdir(page_dir) if f.endswith(".tiff"))
        if not pages:
            raise ScanError("No pages scanned from ADF")
        return [os.path.join(page_dir, p) for p in pages], bool(fed)

    async def scan_feeder_pages(
        self,
        resolution: int = 300,
        mode: str = "Color",
        fmt: str = "jpeg",
        progress_callback: Callable[[str, float], Awaitable[None]] | None = None,
        device: str | None = None,
        scan_dir: str | None = None,
        glass_request: bool = False,
        feeder_callback: Callable[[], None] | None = None,
    ) -> tuple[str, str, str, list[str]]:
        """Scan every feeder sheet and keep each as its own `fmt` file.

        `glass_request` is for a glass request that paper in the feeder takes
        over; `feeder_callback` is called if the feeder served it.

        Returns (scan_id, pdf_path, page_dir, page_files): the pages sit in
        `page_dir`, which the caller owns from here on; `pdf_path` is a merged
        copy for history. Nothing is left behind if this fails or is cancelled.
        """
        if self._lock.locked():
            raise ScanError("Scanner is busy. Try again later.")

        async with self._lock:
            scan_id = str(uuid.uuid4())
            _scan_dir = scan_dir or self._scan_dir
            _device = device or self._scanner_device
            page_dir = os.path.join(_scan_dir, f"pages_{scan_id}")
            pdf_file = os.path.join(_scan_dir, f"{scan_id}.pdf")
            os.makedirs(page_dir, exist_ok=True)

            success = False
            try:
                tiffs, fed = await self._acquire_feeder_tiffs(
                    device=_device, resolution=resolution, mode=mode, page_dir=page_dir,
                    scan_id=scan_id, progress_callback=progress_callback,
                    glass_request=glass_request,
                )
                await _merge_pages_to_pdf(tiffs, pdf_file)
                if fmt == "tiff":
                    files = tiffs
                else:
                    ext = {"jpeg": "jpg"}.get(fmt, fmt)
                    files = []
                    for i, tiff in enumerate(tiffs, 1):
                        out = os.path.join(page_dir, f"page_{i:04d}.{ext}")
                        await asyncio.to_thread(
                            self._convert_scan_sync, tiff, out, fmt, resolution
                        )
                        os.unlink(tiff)
                        files.append(out)
                success = True
                if fed and glass_request and feeder_callback is not None:
                    feeder_callback()
                return scan_id, pdf_file, page_dir, files
            finally:
                if not success:
                    shutil.rmtree(page_dir, ignore_errors=True)
                    if os.path.exists(pdf_file):
                        os.unlink(pdf_file)

    async def scan_batch(
        self,
        resolution: int = 300,
        mode: str = "Color",
        progress_callback: Callable[[str, float], Awaitable[None]] | None = None,
        device: str | None = None,
        scan_dir: str | None = None,
    ) -> tuple[str, str, int]:
        """Perform a multi-page ADF batch scan, merging pages into a single PDF.

        Returns:
            Tuple of (scan_id, output_pdf_path, page_count)
        """
        if self._lock.locked():
            raise ScanError("Scanner is busy. Try again later.")

        async with self._lock:
            scan_id = str(uuid.uuid4())
            pdf_file, count, _ = await self._scan_feeder_pdf(
                device=device or self._scanner_device,
                resolution=resolution,
                mode=mode,
                scan_dir=scan_dir or self._scan_dir,
                scan_id=scan_id,
                progress_callback=progress_callback,
            )
            return scan_id, pdf_file, count


scan_service = ScanService()


async def get_default_scanner_device(db: AsyncSession) -> str:
    """Return the SANE device string for the default scanner (DB overrides settings).

    Raises:
        ScannerBusyError: if there is no default Scanner row AND no
        settings-configured scanner device either -- there is nothing to
        scan with, so callers must not proceed with an empty device string
        (F35).
    """
    scanner = await get_default_scanner(db)
    if scanner is not None:
        return scanner.device
    if scan_service._scanner_device:
        return scan_service._scanner_device
    raise ScannerBusyError("No default scanner configured")


async def get_default_scanner(db: AsyncSession):
    """Return the default Scanner DB object, or None."""
    from app.models import Scanner
    result = await db.execute(select(Scanner).where(Scanner.is_default.is_(True)))
    # F11: .first() rather than scalar_one_or_none() -- defensive against a
    # duplicate is_default=true row despite the partial unique index.
    return result.scalars().first()


def render_scan_filename(template: str, scan_job, fmt: str | None = None) -> str:
    """Render a scan filename from a template string.

    Supported variables:
      {date}       — YYYY-MM-DD
      {time}       — HH-MM-SS
      {datetime}   — YYYY-MM-DD_HH-MM-SS
      {id}         — scan UUID (short: first 8 chars)
      {full_id}    — full scan UUID
      {resolution} — scan DPI
      {mode}       — color mode (Color/Gray/Lineart)
      {format}     — file format (pdf/png/jpeg/tiff)
      {pages}      — page count
      {counter}    — auto-incrementing daily counter (simple: based on scan_id hash)
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    ext = fmt or scan_job.format

    replacements = {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H-%M-%S"),
        "datetime": now.strftime("%Y-%m-%d_%H-%M-%S"),
        "id": scan_job.scan_id[:8],
        "full_id": scan_job.scan_id,
        "resolution": str(scan_job.resolution),
        "mode": scan_job.mode,
        "format": ext,
        "pages": str(scan_job.page_count),
        "counter": str(abs(hash(scan_job.scan_id)) % 10000).zfill(4),
    }

    result = template
    for key, value in replacements.items():
        result = result.replace(f"{{{key}}}", value)

    # Sanitize: remove any chars that aren't safe for filenames
    result = re.sub(r'[^\w\-.]', '_', result)
    return f"{result}.{ext}"


async def run_post_scan_actions(
    scan_job, scanner, db: AsyncSession, *, default_filename_template: str | None = None
) -> None:
    """Run configured auto-deliver actions after a scan completes.

    Each action is independent and best-effort (F21): one delivery target
    being down (bad SMTP password, unreachable FTP host, ...) must not stop
    the others from running, and the scan itself always stays "completed" --
    but a failure is no longer silent. It's logged at warning level and its
    curated name is collected into `failed_actions`, which the caller writes
    onto `scan_job.error_message` (still commits/broadcasts "completed";
    error_message is informational, not a status change) so it's visible
    from the UI/API instead of vanishing with no trace.
    """
    from app.routers.email import _get_smtp_config
    from app.services.cloud_service import cloud_service
    from app.services.email_service import email_service

    if not scanner or not scanner.post_scan_config or not scan_job.filepath:
        return

    config = scanner.post_scan_config
    failed_actions: list[str] = []

    # Use template naming if configured, otherwise fall back to the
    # per-request default the caller resolved from settings (F43) -- never
    # the module singleton, which is no longer kept fresh per-request.
    template = (
        config.get("filename_template")
        or default_filename_template
        or "scan_{date}_{time}_{id}"
    )
    filename = render_scan_filename(template, scan_job)

    # Every delivery below is slow network/subprocess I/O: end whatever
    # transaction the caller left open (and each DB read made along the way)
    # before it, so no connection sits idle-in-transaction during uploads.
    await db.commit()

    # OCR — apply before other delivery actions so recipients get searchable PDF
    if config.get("ocr") and scan_job.format == "pdf" and scan_job.filepath:
        try:
            from app.services.ocr_service import ocr_service
            language = config.get("ocr_language", "eng")
            await ocr_service.apply_ocr(scan_job.filepath, language=language)
        except Exception as exc:
            logger.warning(
                "Post-scan action %s failed for scan %s: %s", "ocr", scan_job.scan_id, exc
            )
            failed_actions.append("ocr")

    if config.get("email"):
        try:
            db_config = await _get_smtp_config(db)
            await db.commit()
            await email_service.send_scan(
                to=config["email"],
                subject=f"Scan: {filename}",
                body="Scan delivered automatically by Papyrus.",
                filepath=scan_job.filepath,
                filename=filename,
                db_config=db_config,
            )
        except Exception as exc:
            logger.warning(
                "Post-scan action %s failed for scan %s: %s", "email", scan_job.scan_id, exc
            )
            failed_actions.append("email")

    if config.get("folder"):
        try:
            dest = os.path.join(config["folder"], filename)
            await asyncio.to_thread(shutil.copy2, scan_job.filepath, dest)
        except Exception as exc:
            logger.warning(
                "Post-scan action %s failed for scan %s: %s", "folder", scan_job.scan_id, exc
            )
            failed_actions.append("folder")

    if config.get("cloud_provider_id"):
        try:
            from sqlalchemy import select as sa_select

            from app.models import CloudProvider
            result = await db.execute(
                sa_select(CloudProvider).where(CloudProvider.id == config["cloud_provider_id"])
            )
            provider = result.scalar_one_or_none()
            await db.commit()
            if provider:
                if provider.provider in ("gdrive", "dropbox"):
                    # F14: refresh the token first if it's expired, rather
                    # than handing the (possibly stale) stored token
                    # straight to the provider SDK -- this path's errors
                    # are best-effort/swallowed below, so a silently-401ing
                    # stale token used to mean the scan just never arrived.
                    access_token = await cloud_service.get_valid_access_token(db, provider)
                if provider.provider == "gdrive":
                    await cloud_service.upload_to_gdrive(
                        filepath=scan_job.filepath,
                        filename=filename,
                        access_token=access_token,
                    )
                elif provider.provider == "dropbox":
                    await cloud_service.upload_to_dropbox(
                        filepath=scan_job.filepath,
                        filename=filename,
                        access_token=access_token,
                    )
                elif provider.provider == "webdav":
                    from app.services.crypto import decrypt_value
                    from app.services.webdav_service import webdav_service
                    combined = decrypt_value(provider.access_token_encrypted)
                    parts = combined.split("||", 1)
                    if len(parts) == 2 and provider.refresh_token_encrypted:
                        webdav_url, webdav_user = parts
                        dest = config.get("webdav_folder", "/")
                        await webdav_service.upload_file(
                            webdav_url, webdav_user, provider.refresh_token_encrypted,
                            scan_job.filepath, filename, dest,
                        )
        except Exception as exc:
            logger.warning(
                "Post-scan action %s failed for scan %s: %s", "cloud", scan_job.scan_id, exc
            )
            failed_actions.append("cloud")

    if config.get("ftp_host"):
        try:
            from app.services.crypto import decrypt_value_lenient, encrypt_value
            from app.services.ftp_service import ftp_service
            host = config["ftp_host"]
            port = int(config.get("ftp_port", 21))
            user = config.get("ftp_username", "")
            # ftp_password is stored encrypted at rest (F5), but a scanner
            # configured before that fix -- or whose secret hasn't been
            # re-saved via PUT since -- may still hold the legacy plaintext
            # value. decrypt_value_lenient() falls back to treating a
            # non-Fernet value as plaintext instead of raising InvalidToken
            # and silently dropping this whole delivery action; the result
            # is re-encrypted fresh so ftp_service's own (strict)
            # decrypt_value() call always succeeds, whichever case this was.
            stored_password = config.get("ftp_password") or ""
            plaintext_password = (
                decrypt_value_lenient(stored_password) if stored_password else ""
            )
            pwd_enc = encrypt_value(plaintext_password)
            remote_dir = config.get("ftp_remote_dir", "/")
            protocol = config.get("ftp_protocol", "ftp")
            if protocol == "sftp":
                from app.routers.settings import get_setting
                # F58: an admin-pinned SHA256 fingerprint of the expected
                # SFTP host key. Global (not per-scanner) setting -- see
                # FtpCard.
                host_key_fingerprint = await get_setting(db, "sftp_host_key_fingerprint")
                await db.commit()
                await ftp_service.upload_sftp(
                    host, port, user, pwd_enc, scan_job.filepath, filename, remote_dir,
                    host_key_fingerprint=host_key_fingerprint,
                )
            else:
                await ftp_service.upload_ftp(
                    host, port, user, pwd_enc, scan_job.filepath, filename, remote_dir,
                    use_tls=(protocol == "ftps"),
                )
        except Exception as exc:
            logger.warning(
                "Post-scan action %s failed for scan %s: %s", "ftp", scan_job.scan_id, exc
            )
            failed_actions.append("ftp")

    if failed_actions:
        scan_job.error_message = f"Delivery failed: {', '.join(failed_actions)}"
        await db.commit()
