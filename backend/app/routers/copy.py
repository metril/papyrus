from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_permission
from app.database import get_db
from app.models import PrintJob, ScanJob, User
from app.schemas import CopyRequest
from app.services.copy_service import CopyError, copy_service
from app.services.ws_manager import ws_manager

router = APIRouter()


@router.post("")
async def create_copy(
    request: CopyRequest,
    user: User = Depends(require_permission("print")),
    db: AsyncSession = Depends(get_db),
):
    """Scan a document and immediately print it (copy workflow)."""
    async def progress_callback(scan_id: str, percent: float):
        await ws_manager.broadcast("jobs", {
            "type": "copy_progress",
            "data": {"scan_id": scan_id, "progress": percent},
        })

    try:
        result = await copy_service.copy(
            resolution=request.resolution,
            mode=request.mode,
            source=request.source,
            copies=request.copies,
            duplex=request.duplex,
            media=request.media,
            progress_callback=progress_callback,
        )
    except CopyError as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Record both the scan and print jobs
    scan_job = ScanJob(
        user_id=user.id,
        scan_id=result["scan_id"],
        status="completed",
        resolution=request.resolution,
        mode=request.mode,
        format="tiff",
        source=request.source,
        filepath=result["filepath"],
        completed_at=datetime.now(timezone.utc),
    )
    db.add(scan_job)

    print_job = PrintJob(
        user_id=user.id,
        cups_job_id=result["cups_job_id"],
        title=f"Copy_{result['scan_id']}",
        filename=f"copy_{result['scan_id']}.tiff",
        filepath=result["filepath"],
        file_size=0,
        mime_type="image/tiff",
        status="printing",
        copies=request.copies,
        duplex=request.duplex,
        media=request.media,
        source_type="upload",
    )
    db.add(print_job)
    await db.commit()

    return {
        "message": "Copy initiated",
        "scan_id": result["scan_id"],
        "cups_job_id": result["cups_job_id"],
    }
