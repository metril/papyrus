import asyncio
import os
import tempfile


CONVERTIBLE_MIMES = {
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.oasis.opendocument.text",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.oasis.opendocument.spreadsheet",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.oasis.opendocument.presentation",
}

PRINTABLE_MIMES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/tiff",
}


def needs_conversion(mime_type: str) -> bool:
    """Check if a file needs conversion to PDF before printing."""
    return mime_type in CONVERTIBLE_MIMES


def is_printable(mime_type: str) -> bool:
    """Check if a file can be printed (directly or after conversion)."""
    return mime_type in PRINTABLE_MIMES or mime_type in CONVERTIBLE_MIMES


async def convert_to_pdf(input_path: str, output_dir: str) -> str:
    """Convert a document to PDF using LibreOffice headless.

    Args:
        input_path: Path to the input file
        output_dir: Directory to write the PDF output

    Returns:
        Path to the converted PDF file

    Raises:
        RuntimeError: If conversion fails
    """
    process = await asyncio.create_subprocess_exec(
        "libreoffice",
        "--headless",
        "--convert-to", "pdf",
        "--outdir", output_dir,
        input_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        error_msg = stderr.decode().strip() if stderr else "Unknown error"
        raise RuntimeError(f"LibreOffice conversion failed: {error_msg}")

    # LibreOffice outputs the PDF with the same base name
    base_name = os.path.splitext(os.path.basename(input_path))[0]
    pdf_path = os.path.join(output_dir, f"{base_name}.pdf")

    if not os.path.exists(pdf_path):
        raise RuntimeError("Conversion produced no output file")

    return pdf_path
