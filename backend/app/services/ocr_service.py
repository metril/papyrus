"""OCR service — applies Tesseract OCR to scanned documents via ocrmypdf."""

import asyncio
import os
import shutil


class OCRError(Exception):
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
        """
        if not os.path.exists(filepath):
            raise OCRError(f"File not found: {filepath}")

        ext = os.path.splitext(filepath)[1].lower()
        if ext != ".pdf":
            raise OCRError("OCR is only supported for PDF files")

        # ocrmypdf writes to a separate output file, then we replace the original
        out_path = filepath + ".ocr.pdf"

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
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            # Clean up partial output
            if os.path.exists(out_path):
                os.unlink(out_path)
            stderr_text = stderr.decode().strip()
            raise OCRError(f"ocrmypdf failed (code {process.returncode}): {stderr_text}")

        # Replace original with OCR'd version
        shutil.move(out_path, filepath)
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
