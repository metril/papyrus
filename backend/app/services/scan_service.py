import asyncio
import logging
import os
import re
import shutil
import uuid
from typing import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import PapyrusError, ScannerBusyError

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
        candidates = _scanimage_match_candidates(_device) if _device else []

        return {
            "available": any(c in output for c in candidates),
            "device": _device,
            "output": output.strip(),
        }

    async def get_options(self) -> dict:
        """Get available scanner options for the configured device."""
        return {
            "resolutions": [75, 100, 150, 200, 300, 600],
            "modes": ["Color", "Gray", "Lineart"],
            "formats": ["png", "jpeg", "tiff", "pdf"],
            "sources": ["Flatbed", "ADF"],
        }

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
        on_process_start: Callable[[asyncio.subprocess.Process], None] | None = None,
        left_mm: float | None = None,
        top_mm: float | None = None,
        width_mm: float | None = None,
        height_mm: float | None = None,
    ) -> tuple[str, str]:
        """Perform a single-page scan.

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
            on_process_start: optional callback invoked with the running
                `scanimage` subprocess as soon as it's spawned, so a caller
                that tracks in-flight jobs (eSCL) can keep a handle to kill
                it directly if needed (F110).

        Returns:
            Tuple of (scan_id, output_filepath)
        """
        if self._lock.locked():
            raise ScanError("Scanner is busy. Try again later.")

        async with self._lock:
            scan_id = str(uuid.uuid4())
            _scan_dir = scan_dir or self._scan_dir
            _device = device or self._scanner_device

            # brscan4 uses "FlatBed" (capital B); map common "Flatbed" spelling
            _source = source
            if _device.startswith("brother4:") and source.lower() == "flatbed":
                _source = "FlatBed"

            # brscan4 uses different mode names than standard SANE
            _mode = mode
            if _device.startswith("brother4:"):
                _mode = {
                    "Color": "24bit Color", "Gray": "True Gray", "Lineart": "Black & White",
                }.get(mode, mode)

            # Scan to TIFF as intermediate format, then convert to requested output
            tiff_file = os.path.join(_scan_dir, f"{scan_id}.tiff")

            cmd = [
                "scanimage",
                "-d", _device,
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
            if on_process_start is not None:
                on_process_start(process)

            keep_tiff = False
            try:
                stderr_lines = await self._read_scan_output(
                    process, scan_id, progress_callback
                )

                if process.returncode != 0:
                    stderr_text = "; ".join(ln for ln in stderr_lines if ln)
                    raise ScanError(
                        f"scanimage exited with code {process.returncode}"
                        + (f": {stderr_text}" if stderr_text else "")
                    )

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

    @staticmethod
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

        return await self._await_with_timeout(
            _read(), process, _SCAN_TIMEOUT_SECONDS, "Scan timed out"
        )

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
            _scan_dir = scan_dir or self._scan_dir
            page_dir = os.path.join(_scan_dir, f"batch_{scan_id}")
            os.makedirs(page_dir, exist_ok=True)

            _device = device or self._scanner_device
            # scanimage --batch mode scans all pages from ADF
            cmd = [
                "scanimage",
                "-d", _device,
                "--resolution", str(resolution),
                "--mode", mode,
                "--format=tiff",
                "--source", "ADF",
                "--batch", os.path.join(page_dir, "page_%04d.tiff"),
                "--progress",
            ]

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            success = False
            try:
                async def _read_batch() -> None:
                    if process.stderr:
                        async for line in process.stderr:
                            text = line.decode().strip()
                            match = re.search(r"Progress: (\d+\.?\d*)%", text)
                            if match and progress_callback:
                                await progress_callback(scan_id, float(match.group(1)))
                    await process.wait()

                await self._await_with_timeout(
                    _read_batch(), process, _BATCH_SCAN_TIMEOUT_SECONDS, "Batch scan timed out"
                )

                # scanimage returns non-zero when ADF runs out of paper, which is expected
                # Check if we got any pages
                pages = sorted(
                    f for f in os.listdir(page_dir) if f.endswith(".tiff")
                )

                if not pages:
                    raise ScanError("No pages scanned from ADF")

                # Merge all pages into a single PDF
                pdf_file = os.path.join(_scan_dir, f"{scan_id}.pdf")
                page_paths = [os.path.join(page_dir, p) for p in pages]

                pdf_process = await asyncio.create_subprocess_exec(
                    "img2pdf", *page_paths, "-o", pdf_file,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await self._await_with_timeout(
                    pdf_process.wait(), pdf_process,
                    _PDF_MERGE_TIMEOUT_SECONDS, "PDF merge timed out",
                )

                if pdf_process.returncode != 0:
                    raise ScanError("Failed to merge pages into PDF")

                # Clean up individual page files
                for p in page_paths:
                    os.unlink(p)
                os.rmdir(page_dir)

                success = True
                return scan_id, pdf_file, len(pages)
            finally:
                # F22: any non-success exit leaks page_dir and every page
                # TIFF in it (up to hundreds of MB for a large ADF batch)
                # unless cleaned up here -- nothing references page_dir from
                # the DB, so retention can never reclaim it either.
                if not success:
                    shutil.rmtree(page_dir, ignore_errors=True)


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
            if provider:
                if provider.provider == "gdrive":
                    await cloud_service.upload_to_gdrive(
                        filepath=scan_job.filepath,
                        filename=filename,
                        access_token_encrypted=provider.access_token_encrypted,
                    )
                elif provider.provider == "dropbox":
                    await cloud_service.upload_to_dropbox(
                        filepath=scan_job.filepath,
                        filename=filename,
                        access_token_encrypted=provider.access_token_encrypted,
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
                await ftp_service.upload_sftp(
                    host, port, user, pwd_enc, scan_job.filepath, filename, remote_dir
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
