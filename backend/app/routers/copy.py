import asyncio
import logging
import os
import shutil
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_docs import (
    AUTH_ERRORS,
    BAD_GATEWAY,
    CONFLICT,
    NOT_FOUND,
    UNAVAILABLE,
    VALIDATION,
    std_responses,
)
from app.auth.dependencies import require_permission
from app.database import get_db
from app.exceptions import (
    CopyCancelledError,
    CopyIdInUseError,
    CopyNotRunningError,
    NotFoundError,
    ScanCancelledError,
)
from app.models import PrintJob, ScanJob, User
from app.schemas import (
    CopyRequest,
    CopyResponse,
    CopyStatus,
    serialize_print_job,
    serialize_scan_job,
)
from app.services import scan_tasks
from app.services.audit_service import log_event
from app.services.copy_service import CopyError, copy_service
from app.services.cups_service import CupsService, get_default_release_queue_name
from app.services.file_service import get_upload_path
from app.services.scan_service import get_default_scanner_device
from app.services.ws_manager import ws_manager

logger = logging.getLogger(__name__)
router = APIRouter()


def _status(entry: scan_tasks.CopyEntry) -> CopyStatus:
    pct = entry.progress_pct
    if entry.phase == "scanning" and entry.scan_id:
        live, _ = scan_tasks.snapshot(entry.scan_id)
        pct = live if live is not None else pct
    return CopyStatus(
        copy_id=entry.copy_id, phase=entry.phase, progress_pct=pct,
        scan_id=entry.scan_id, job_id=entry.job_id,
    )


def _get_entry(copy_id: uuid.UUID, user: User) -> scan_tasks.CopyEntry:
    entry = scan_tasks.get_copy(str(copy_id))
    if entry is None or not (entry.owner_id == user.id or user.role == "admin"):
        raise NotFoundError("Copy not found")
    return entry


@router.post(
    "",
    response_model=CopyResponse,
    summary="Copy a document",
    responses=std_responses(AUTH_ERRORS, CONFLICT, VALIDATION, BAD_GATEWAY, UNAVAILABLE),
)
async def create_copy(
    request: CopyRequest,
    user: User = Depends(require_permission("print")),
    db: AsyncSession = Depends(get_db),
):
    """Scan a document and immediately print it (copy workflow).

    Requires the `print` permission. Side effects: creates a ScanJob and a PrintJob
    (broadcast as `scan_completed` / `job_created` WebSocket events). Returns 409 when a
    feeder request finds the feeder empty (or the supplied `copy_id` is in use, code
    `copy_id_in_use`), 409 `scan_cancelled` when cancelled while scanning (see
    `POST /copy/{copy_id}/cancel`), and 503 when the scanner or printer is unavailable.

    Poll `GET /copy/{copy_id}` for the phase and scan progress; the response carries `copy_id`.
    """
    from app.routers.scanner import run_scan_task
    from app.routers.settings import get_setting

    entry = scan_tasks.register_copy(str(request.copy_id) if request.copy_id else None, user.id)
    if entry is None:
        raise CopyIdInUseError("This copy_id is already in use")

    scan_job: ScanJob | None = None
    try:
        queue = await get_default_release_queue_name(db)
        device = await get_default_scanner_device(db)
        # F43: resolved fresh per request rather than relying on the
        # scan_service singleton's default, which is no longer kept in sync.
        scan_dir = await get_setting(db, "scan_dir") or "/app/data/scans"
        cups = CupsService(printer_name=queue)

        # The scan row exists from the start so the scan can be tracked and cancelled by id.
        scan_job = ScanJob(
            user_id=user.id, status="scanning", resolution=request.resolution,
            mode=request.mode, source=request.source,
            format="pdf" if request.source == "ADF" else "tiff",
        )
        db.add(scan_job)
        await db.commit()
        await db.refresh(scan_job)
        scan_job_id = scan_job.scan_id
        entry.scan_id = scan_job_id
        # End the read transaction: the scan below can run for minutes and must
        # not hold an idle-in-transaction session (and its locks) meanwhile.
        await db.commit()

        async def run_scan(coro):
            return await run_scan_task(db, scan_job, coro)

        async def progress_callback(_scan_id: str, percent: float):
            scan_tasks.set_progress(scan_job_id, percent)

        def on_page_count(count: int) -> None:
            scan_tasks.set_pages(scan_job_id, count)

        async def record_scan(info: dict) -> None:
            scan_job.status = "completed"
            scan_job.format = info["format"]
            scan_job.page_count = info["page_count"]
            scan_job.source = info["source"]
            scan_job.filepath = info["filepath"]
            scan_job.completed_at = datetime.now(timezone.utc)
            await db.commit()
            await db.refresh(scan_job)
            await db.commit()  # refresh reopened a transaction; printing comes next
            entry.phase = "printing"
            entry.progress_pct = None
            await ws_manager.broadcast_scan({
                "type": "scan_completed",
                "data": serialize_scan_job(scan_job),
            }, scan_job.user_id)

        def on_cups_job(cups_job_id: int) -> None:
            entry.cups_job_id = cups_job_id

        result = await copy_service.copy(
            cups=cups,
            device=device,
            resolution=request.resolution,
            mode=request.mode,
            source=request.source,
            copies=request.copies,
            duplex=request.duplex,
            media=request.media,
            scan_dir=scan_dir,
            on_scanned=record_scan,
            progress_callback=progress_callback,
            run_scan=run_scan,
            on_page_count=on_page_count,
            on_cups_job=on_cups_job,
        )

        # The print job gets its own copy of the scanned file rather than
        # aliasing the scan's path (F29) — deleting either row's file must not
        # take the other row's file with it.
        upload_dir = await get_setting(db, "upload_dir") or "/app/data/uploads"
        print_filename = f"copy_{result['scan_id']}.{result['format']}"
        print_filepath = get_upload_path(print_filename, upload_dir=upload_dir)
        os.makedirs(os.path.dirname(print_filepath), exist_ok=True)
        await asyncio.to_thread(shutil.copy2, result["filepath"], print_filepath)

        print_job = PrintJob(
            user_id=user.id,
            cups_job_id=result["cups_job_id"],
            title=f"Copy_{result['scan_id']}",
            filename=print_filename,
            filepath=print_filepath,
            file_size=0,
            mime_type="application/pdf" if result["format"] == "pdf" else "image/tiff",
            status="cancelled" if entry.cancel_requested else "printing",
            copies=request.copies,
            duplex=request.duplex,
            media=request.media,
            source_type="upload",
        )
        db.add(print_job)
        await db.commit()
        if entry.cancel_requested:
            await _cancel_cups(db, result["cups_job_id"])

        # Surface the print record to connected clients incrementally, or it
        # wouldn't appear until a manual refetch.
        await db.refresh(print_job)
        entry.job_id = print_job.id
        await ws_manager.broadcast("jobs", {
            "type": "job_created",
            "data": serialize_print_job(print_job),
        })
    except ScanCancelledError:
        scan_tasks.finish_copy(entry, "cancelled")
        raise
    except asyncio.CancelledError:
        scan_tasks.finish_copy(entry, "cancelled")
        raise
    except Exception as exc:
        if entry.cancel_requested and isinstance(exc, CopyError):
            scan_tasks.finish_copy(entry, "cancelled")
            raise CopyCancelledError() from None
        scan_tasks.finish_copy(entry, "failed")
        if scan_job is not None and scan_job.status == "scanning":
            try:
                scan_job.status = "failed"
                scan_job.error_message = getattr(exc, "detail", None) or "Copy failed"
                await db.commit()
            except Exception:
                logger.exception("Could not mark copy scan failed")
        raise

    if not entry.cancel_requested:
        scan_tasks.finish_copy(entry, "done")
    return {
        "message": "Copy initiated",
        "scan_id": scan_job_id,
        "cups_job_id": result["cups_job_id"],
        "copy_id": entry.copy_id,
    }


async def _cancel_cups(db: AsyncSession, cups_job_id: int) -> None:
    """Best-effort cancel of a CUPS job on the default release queue."""
    try:
        queue = await get_default_release_queue_name(db)
        await CupsService(printer_name=queue).cancel_job(cups_job_id)
    except Exception:
        logger.warning("Could not cancel CUPS job %s", cups_job_id, exc_info=True)


@router.get(
    "/{copy_id}",
    response_model=CopyStatus,
    summary="Get copy status",
    responses=std_responses(AUTH_ERRORS, NOT_FOUND, VALIDATION),
)
async def get_copy_status(
    copy_id: uuid.UUID,
    user: User = Depends(require_permission("print")),
):
    """Phase, scan progress and ids of a copy started with `POST /copy`.

    Requires the `print` permission; the owner or an admin only (404 otherwise, also for an unknown
    or expired id). Finished copies stay queryable for about 10 minutes. State is kept in process
    memory, so it is lost on restart.
    """
    return _status(_get_entry(copy_id, user))


@router.post(
    "/{copy_id}/cancel",
    response_model=CopyStatus,
    status_code=202,
    summary="Cancel a copy",
    responses=std_responses(AUTH_ERRORS, NOT_FOUND, CONFLICT, VALIDATION),
)
async def cancel_copy(
    copy_id: uuid.UUID,
    user: User = Depends(require_permission("print")),
    db: AsyncSession = Depends(get_db),
):
    """Cancel a copy that is scanning or printing.

    Requires the `print` permission; owner or admin only (404 otherwise). While scanning the scan
    is cancelled (the original `POST /copy` fails with 409 `scan_cancelled`); while printing the
    CUPS job is cancelled and the print job marked `cancelled`. A finished copy returns 409
    (`copy_not_running`).
    """
    entry = _get_entry(copy_id, user)
    if entry.phase in scan_tasks.TERMINAL_PHASES:
        raise CopyNotRunningError("This copy has already finished")

    if entry.phase == "scanning":
        entry.cancel_requested = True
        if not (entry.scan_id and scan_tasks.cancel(entry.scan_id)):
            scan_tasks.finish_copy(entry, "cancelled")
    else:
        entry.cancel_requested = True
        scan_tasks.finish_copy(entry, "cancelled")
        if entry.cups_job_id is not None:
            await _cancel_cups(db, entry.cups_job_id)
        if entry.job_id is not None:
            job = await db.get(PrintJob, entry.job_id)
            if job is not None:
                job.status = "cancelled"
                await db.commit()
                await db.refresh(job)
                await ws_manager.broadcast("jobs", {
                    "type": "job_updated", "data": serialize_print_job(job),
                })
    await log_event(db, "copy.cancel", "copy", entry.copy_id, user_id=user.id)
    await db.commit()
    return _status(entry)
