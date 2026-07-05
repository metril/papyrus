"""Printer identify sheet: render a one-page test PDF and print it immediately.

Lets an admin physically confirm which device a configured `Printer` row maps
to by printing a sheet showing its display name, model, location, URI, and
CUPS queue. Mirrors the upload/release conventions in `app.routers.jobs`:
same upload directory, same held -> printing/failed status transitions, same
full-object WS broadcasts on the `jobs` channel.
"""
import asyncio
import io
import os
from datetime import datetime, timezone

import img2pdf
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Printer, PrintJob, User
from app.schemas import serialize_print_job
from app.services.cups_service import CupsService
from app.services.file_service import get_upload_path
from app.services.ws_manager import ws_manager

# A4-proportioned canvas at ~150 dpi (210mm x 297mm).
_PAGE_WIDTH = 1240
_PAGE_HEIGHT = 1754
_MARGIN = 100


class TestPageError(Exception):
    """Raised when CUPS fails to print the rendered test page.

    The PrintJob row is already marked ``failed`` (and broadcast) by the time
    this is raised; the router maps it to a 502.
    """


def _load_font(size: int) -> ImageFont.ImageFont:
    """Load the PIL built-in font at `size` (Pillow >= 10.1 supports sizing
    the default font); fall back to the fixed-size default on older Pillow."""
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _render_test_page_pdf(printer: Printer) -> bytes:
    """Render the identify sheet and return it as PDF bytes.

    Synchronous/CPU-bound (PIL drawing + PNG encode + img2pdf conversion);
    callers must run this inside ``asyncio.to_thread``.
    """
    image = Image.new("RGB", (_PAGE_WIDTH, _PAGE_HEIGHT), "white")
    draw = ImageDraw.Draw(image)

    heading_font = _load_font(40)
    name_font = _load_font(64)
    label_font = _load_font(30)
    footer_font = _load_font(24)

    y = _MARGIN
    draw.text((_MARGIN, y), "Papyrus Test Page", fill="black", font=heading_font)
    y += 90

    draw.text((_MARGIN, y), printer.display_name, fill="black", font=name_font)
    y += 110

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        f"Model: {printer.make_and_model or 'Unknown'}",
        f"Location: {printer.location or 'Unknown'}",
        f"URI: {printer.uri or 'Unknown'}",
        f"CUPS queue: {printer.cups_name}",
        f"Printed: {timestamp}",
    ]
    for line in lines:
        draw.text((_MARGIN, y), line, fill="black", font=label_font)
        y += 48

    draw.text(
        (_MARGIN, _PAGE_HEIGHT - _MARGIN),
        "Printed via Papyrus",
        fill="black",
        font=footer_font,
    )

    png_buffer = io.BytesIO()
    image.save(png_buffer, format="PNG")
    return img2pdf.convert(png_buffer.getvalue())


def _write_pdf(filepath: str, pdf_bytes: bytes) -> None:
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "wb") as f:
        f.write(pdf_bytes)


async def print_test_page(db: AsyncSession, printer: Printer, user: User) -> PrintJob:
    """Render an identify sheet for `printer` and print it immediately.

    Creates a `held` PrintJob row and broadcasts `job_created`, then submits
    it straight to the printer's CUPS release queue (the same queue used by
    the normal release flow -- the printer's own hold queue only feeds jobs
    back into the app via the papyrus CUPS backend, so it can't be used to
    physically print). On success the job moves to `printing` and broadcasts
    `job_updated`; on CUPS failure it moves to `failed`, broadcasts
    `job_updated`, and this function raises `TestPageError` so the caller can
    surface an error while the failed job remains as history.
    """
    pdf_bytes = await asyncio.to_thread(_render_test_page_pdf, printer)

    from app.routers.settings import get_setting
    upload_dir = await get_setting(db, "upload_dir") or "/app/data/uploads"
    filepath = get_upload_path("test-page.pdf", upload_dir=upload_dir)
    await asyncio.to_thread(_write_pdf, filepath, pdf_bytes)

    job = PrintJob(
        user_id=user.id,
        title=f"Test page — {printer.display_name}",
        filename="test-page.pdf",
        filepath=filepath,
        file_size=len(pdf_bytes),
        mime_type="application/pdf",
        status="held",
        copies=1,
        duplex=False,
        media="A4",
        source_type="test_page",
        printer_id=printer.id,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    await ws_manager.broadcast("jobs", {
        "type": "job_created",
        "data": serialize_print_job(job),
    })

    release_queue = f"{printer.cups_name}_release"
    svc = CupsService(printer_name=release_queue)

    try:
        cups_job_id = await svc.create_held_job(
            filepath=filepath,
            title=job.title,
            copies=1,
            duplex=False,
            media="A4",
        )
        job.cups_job_id = cups_job_id
        await svc.release_job(cups_job_id)

        job.status = "printing"
        await db.commit()
        await db.refresh(job)
        await ws_manager.broadcast("jobs", {
            "type": "job_updated", "data": serialize_print_job(job)
        })
    except Exception as exc:
        job.status = "failed"
        job.error_message = str(exc)
        await db.commit()
        await db.refresh(job)
        await ws_manager.broadcast("jobs", {
            "type": "job_updated", "data": serialize_print_job(job)
        })
        raise TestPageError(str(exc)) from exc

    return job
