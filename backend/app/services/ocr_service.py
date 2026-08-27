"""OCR service — applies Tesseract OCR to scanned documents via ocrmypdf."""

import asyncio
import os
from uuid import uuid4

from app.exceptions import ExternalServiceError
from app.services.file_locks import lock_for

_OCR_TIMEOUT_SECONDS = 600


class OCRError(ExternalServiceError):
    pass


class OCRService:
    async def apply_ocr(
        self,
        filepath: str,
        language: str = "eng",
        deskew: bool = True,
    ) -> str:
        """Apply OCR to a PDF file, producing a searchable PDF.

        If the input is already a searchable PDF, it is returned unchanged
        (--skip-text flag).

        Returns the path to the OCR'd file (replaces original in-place).

        The rewrite is serialized per-path (F12): manual "Apply OCR" and
        auto-deliver OCR can otherwise overlap (the scan is broadcast
        "completed" before auto-deliver runs), and two concurrent ocrmypdf
        processes both targeting a shared, deterministic ``.ocr.pdf`` name
        used to be able to stomp on each other. The output path is now
        uuid-suffixed so even a caller that skipped the lock can't collide
        with another in-flight OCR run.
        """
        if not os.path.exists(filepath):
            raise OCRError(f"File not found: {filepath}")

        ext = os.path.splitext(filepath)[1].lower()
        if ext != ".pdf":
            raise OCRError("OCR is only supported for PDF files")

        async with lock_for(filepath):
            return await self._apply_ocr_locked(filepath, language=language, deskew=deskew)

    async def _apply_ocr_locked(self, filepath: str, *, language: str, deskew: bool) -> str:
        # ocrmypdf writes to a unique output file, then we replace the
        # original with os.replace (atomic on the same filesystem).
        out_path = f"{filepath}.{uuid4().hex}.ocr.pdf"

        cmd = [
            "ocrmypdf",
            "--language", language,
            "--skip-text",
            "--jobs", "2",
        ]
        if deskew:
            cmd.append("--deskew")

        cmd += [filepath, out_path]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=_OCR_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            if os.path.exists(out_path):
                os.unlink(out_path)
            raise OCRError("ocrmypdf timed out")

        if process.returncode != 0:
            # Clean up partial output
            if os.path.exists(out_path):
                os.unlink(out_path)
            stderr_text = stderr.decode().strip()
            raise OCRError(f"ocrmypdf failed (code {process.returncode}): {stderr_text}")

        # Replace original with OCR'd version. os.replace (not shutil.move) is
        # an atomic rename on the same filesystem -- out_path was written
        # alongside filepath, so this never leaves a half-written file visible
        # at `filepath`.
        os.replace(out_path, filepath)
        return filepath

    async def is_available(self) -> bool:
        """Check if ocrmypdf is installed and available."""
        try:
            process = await asyncio.create_subprocess_exec(
                "ocrmypdf", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await process.communicate()
            return process.returncode == 0
        except FileNotFoundError:
            return False


ocr_service = OCRService()
