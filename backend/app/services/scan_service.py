import asyncio
import os
import re
import uuid
from typing import Callable, Awaitable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings


class ScanError(Exception):
    pass


class ScanService:
    def __init__(self):
        self._lock = asyncio.Lock()

    async def check_device(self) -> dict:
        """Check if the scanner device is available."""
        process = await asyncio.create_subprocess_exec(
            "scanimage", "-L",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        output = stdout.decode() + stderr.decode()
        device = settings.scanner_device

        return {
            "available": device.split(":")[-1].strip() in output or process.returncode == 0,
            "device": device,
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

    async def scan(
        self,
        resolution: int = 300,
        mode: str = "Color",
        fmt: str = "pdf",
        source: str = "Flatbed",
        progress_callback: Callable[[str, float], Awaitable[None]] | None = None,
        device: str | None = None,
    ) -> tuple[str, str]:
        """Perform a single-page scan.

        Args:
            resolution: DPI (75-600)
            mode: Color, Gray, or Lineart
            fmt: Output format (png, jpeg, tiff, pdf)
            source: Flatbed or ADF
            progress_callback: Async callback(scan_id, percent)

        Returns:
            Tuple of (scan_id, output_filepath)
        """
        if self._lock.locked():
            raise ScanError("Scanner is busy. Try again later.")

        async with self._lock:
            scan_id = str(uuid.uuid4())
            _device = device or settings.scanner_device

            # brscan4 uses "FlatBed" (capital B); map common "Flatbed" spelling
            _source = source
            if _device.startswith("brother4:") and source.lower() == "flatbed":
                _source = "FlatBed"

            # brscan4 uses different mode names than standard SANE
            _mode = mode
            if _device.startswith("brother4:"):
                _mode = {"Color": "24bit Color", "Gray": "True Gray", "Lineart": "Black & White"}.get(mode, mode)

            # Always scan to TIFF; brscan4 only reliably outputs TIFF natively
            tiff_file = os.path.join(settings.scan_dir, f"{scan_id}.tiff")

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

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # Collect stderr lines and watch for progress updates
            stderr_lines: list[str] = []
            if process.stderr:
                async for line in process.stderr:
                    text = line.decode().strip()
                    stderr_lines.append(text)
                    match = re.search(r"Progress: (\d+\.?\d*)%", text)
                    if match and progress_callback:
                        await progress_callback(scan_id, float(match.group(1)))

            await process.wait()

            if process.returncode != 0:
                stderr_text = "; ".join(l for l in stderr_lines if l)
                raise ScanError(
                    f"scanimage exited with code {process.returncode}"
                    + (f": {stderr_text}" if stderr_text else "")
                )

            if not os.path.exists(tiff_file):
                raise ScanError("Scan produced no output file")

            # Convert TIFF to the requested format using Pillow
            # (Pillow handles JPEG-in-TIFF from brscan4; img2pdf rejects lossy TIFF)
            if fmt in ("pdf", "png", "jpeg"):
                from PIL import Image
                ext = {"jpeg": "jpg"}.get(fmt, fmt)  # jpeg→jpg, pdf→pdf, png→png
                out_file = os.path.join(settings.scan_dir, f"{scan_id}.{ext}")
                with Image.open(tiff_file) as img:
                    # Ensure mode is compatible with target format
                    if fmt == "jpeg" and img.mode not in ("RGB", "L"):
                        img = img.convert("RGB")
                    elif fmt in ("pdf", "png") and img.mode not in ("RGB", "L", "RGBA"):
                        img = img.convert("RGB")
                    # Embed correct DPI so clients (e.g. macOS Image Capture) can
                    # determine physical size and render the preview correctly
                    if fmt == "pdf":
                        img.save(out_file, resolution=resolution)
                    else:
                        img.save(out_file, dpi=(resolution, resolution))
                os.unlink(tiff_file)
                return scan_id, out_file
            else:
                # tiff — return as-is
                return scan_id, tiff_file

    async def scan_batch(
        self,
        resolution: int = 300,
        mode: str = "Color",
        progress_callback: Callable[[str, float], Awaitable[None]] | None = None,
        device: str | None = None,
    ) -> tuple[str, str, int]:
        """Perform a multi-page ADF batch scan, merging pages into a single PDF.

        Returns:
            Tuple of (scan_id, output_pdf_path, page_count)
        """
        if self._lock.locked():
            raise ScanError("Scanner is busy. Try again later.")

        async with self._lock:
            scan_id = str(uuid.uuid4())
            page_dir = os.path.join(settings.scan_dir, f"batch_{scan_id}")
            os.makedirs(page_dir, exist_ok=True)

            _device = device or settings.scanner_device
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

            if process.stderr:
                async for line in process.stderr:
                    text = line.decode().strip()
                    match = re.search(r"Progress: (\d+\.?\d*)%", text)
                    if match and progress_callback:
                        await progress_callback(scan_id, float(match.group(1)))

            await process.wait()

            # scanimage returns non-zero when ADF runs out of paper, which is expected
            # Check if we got any pages
            pages = sorted(
                f for f in os.listdir(page_dir) if f.endswith(".tiff")
            )

            if not pages:
                raise ScanError("No pages scanned from ADF")

            # Merge all pages into a single PDF
            pdf_file = os.path.join(settings.scan_dir, f"{scan_id}.pdf")
            page_paths = [os.path.join(page_dir, p) for p in pages]

            pdf_process = await asyncio.create_subprocess_exec(
                "img2pdf", *page_paths, "-o", pdf_file,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await pdf_process.wait()

            if pdf_process.returncode != 0:
                raise ScanError("Failed to merge pages into PDF")

            # Clean up individual page files
            for p in page_paths:
                os.unlink(p)
            os.rmdir(page_dir)

            return scan_id, pdf_file, len(pages)


scan_service = ScanService()


async def get_default_scanner_device(db: AsyncSession) -> str:
    """Return the SANE device string for the default scanner (DB overrides settings)."""
    from app.models import Scanner  # avoid circular import at module level
    result = await db.execute(select(Scanner).where(Scanner.is_default == True))
    s = result.scalar_one_or_none()
    return s.device if s else settings.scanner_device


async def get_default_scanner(db: AsyncSession):
    """Return the default Scanner DB object, or None."""
    from app.models import Scanner
    result = await db.execute(select(Scanner).where(Scanner.is_default == True))
    return result.scalar_one_or_none()


async def run_post_scan_actions(scan_job, scanner, db: AsyncSession) -> None:
    """Run configured auto-deliver actions after a scan completes."""
    import shutil
    from app.services.email_service import email_service, EmailError
    from app.services.cloud_service import cloud_service, CloudError
    from app.routers.email import _get_smtp_config

    if not scanner or not scanner.post_scan_config or not scan_job.filepath:
        return

    config = scanner.post_scan_config
    filename = f"scan_{scan_job.scan_id}.{scan_job.format}"

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
        except (EmailError, Exception):
            pass

    if config.get("folder"):
        try:
            dest = os.path.join(config["folder"], filename)
            shutil.copy2(scan_job.filepath, dest)
        except Exception:
            pass

    if config.get("cloud_provider_id"):
        try:
            from app.models import CloudProvider
            from sqlalchemy import select as sa_select
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
        except (CloudError, Exception):
            pass
