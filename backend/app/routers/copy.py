import asyncio
import os
import shutil
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_docs import AUTH_ERRORS, BAD_GATEWAY, CONFLICT, UNAVAILABLE, VALIDATION, std_responses
from app.auth.dependencies import require_permission
from app.database import get_db
from app.models import PrintJob, ScanJob, User
from app.schemas import CopyRequest, CopyResponse, serialize_print_job, serialize_scan_job
from app.services.copy_service import copy_service
from app.services.cups_service import CupsService, get_default_release_queue_name
from app.services.file_service import get_upload_path
from app.services.scan_service import get_default_scanner_device
from app.services.ws_manager import ws_manager

router = APIRouter()


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
    feeder request finds the feeder empty and 503 when the scanner or printer is
    unavailable.

    Resolves the default printer's release queue and default scanner device
    from the DB (F10) — the service used to fall back to never-configured
    module singletons (empty printer name, empty scanner device), so every
    copy failed. No progress broadcast (F139): nothing consumed the old
    `copy_progress` jobs-channel frames, which also violated the
    full-serialized-object WS contract.
    """
    from app.routers.settings import get_setting

    queue = await get_default_release_queue_name(db)
    device = await get_default_scanner_device(db)
    # F43: resolved fresh per request rather than relying on the
    # scan_service singleton's default, which is no longer kept in sync.
    scan_dir = await get_setting(db, "scan_dir") or "/app/data/scans"
    cups = CupsService(printer_name=queue)

    async def record_scan(info: dict) -> None:
        scan_job = ScanJob(
            user_id=user.id,
            scan_id=info["scan_id"],
            status="completed",
            resolution=request.resolution,
            mode=request.mode,
            format=info["format"],
            page_count=info["page_count"],
            source=info["source"],
            filepath=info["filepath"],
            completed_at=datetime.now(timezone.utc),
        )
        db.add(scan_job)
        await db.commit()
        await db.refresh(scan_job)
        await ws_manager.broadcast("scans", {
            "type": "scan_completed",
            "data": serialize_scan_job(scan_job),
        })

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
        status="printing",
        copies=request.copies,
        duplex=request.duplex,
        media=request.media,
        source_type="upload",
    )
    db.add(print_job)
    await db.commit()

    # Surface the print record to connected clients incrementally, or it
    # wouldn't appear until a manual refetch.
    await db.refresh(print_job)
    await ws_manager.broadcast("jobs", {
        "type": "job_created",
        "data": serialize_print_job(print_job),
    })

    return {
        "message": "Copy initiated",
        "scan_id": result["scan_id"],
        "cups_job_id": result["cups_job_id"],
    }
