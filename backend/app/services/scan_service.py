import asyncio
import os
import re
import uuid
from typing import Callable, Awaitable

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
            # For PDF, scan as TIFF then convert
            scan_fmt = "tiff" if fmt == "pdf" else fmt
            output_file = os.path.join(settings.scan_dir, f"{scan_id}.{scan_fmt}")

            cmd = [
                "scanimage",
                "-d", settings.scanner_device,
                "--resolution", str(resolution),
                "--mode", mode,
                f"--format={scan_fmt}",
                "--source", source,
                "--progress",
                "-o", output_file,
            ]

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # Parse progress from stderr
            if process.stderr:
                async for line in process.stderr:
                    text = line.decode().strip()
                    match = re.search(r"Progress: (\d+\.?\d*)%", text)
                    if match and progress_callback:
                        await progress_callback(scan_id, float(match.group(1)))

            await process.wait()

            if process.returncode != 0:
                raise ScanError(f"scanimage exited with code {process.returncode}")

            if not os.path.exists(output_file):
                raise ScanError("Scan produced no output file")

            # Convert to PDF if requested
            if fmt == "pdf":
                pdf_file = os.path.join(settings.scan_dir, f"{scan_id}.pdf")
                pdf_process = await asyncio.create_subprocess_exec(
                    "img2pdf", output_file, "-o", pdf_file,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await pdf_process.wait()
                if pdf_process.returncode != 0:
                    raise ScanError("PDF conversion failed")
                os.unlink(output_file)
                output_file = pdf_file

            return scan_id, output_file

    async def scan_batch(
        self,
        resolution: int = 300,
        mode: str = "Color",
        progress_callback: Callable[[str, float], Awaitable[None]] | None = None,
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

            # scanimage --batch mode scans all pages from ADF
            cmd = [
                "scanimage",
                "-d", settings.scanner_device,
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
