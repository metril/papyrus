import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_permission
from app.database import get_db
from app.models import ScanJob, User
from app.schemas import ScanBatchRequest, ScanList, ScanRequest, ScanResponse
from app.services.file_service import cleanup_file
from app.services.scan_service import ScanError, scan_service
from app.services.ws_manager import ws_manager

router = APIRouter()


@router.get("/status")
async def get_scanner_status(user: User = Depends(get_current_user)):
    """Check if the scanner device is available."""
    return await scan_service.check_device()


@router.get("/options")
async def get_scanner_options(user: User = Depends(get_current_user)):
    """Get available scan options."""
    return await scan_service.get_options()


@router.post("/scan", response_model=ScanResponse, status_code=201)
async def initiate_scan(
    request: ScanRequest,
    user: User = Depends(require_permission("scan")),
    db: AsyncSession = Depends(get_db),
):
    """Initiate a single-page scan."""
    # Create scan job record
    job = ScanJob(
        user_id=user.id,
        resolution=request.resolution,
        mode=request.mode,
        format=request.format,
        source=request.source,
        status="scanning",
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    async def progress_callback(scan_id: str, percent: float):
        await ws_manager.broadcast(f"scan:{job.scan_id}", {
            "type": "scan_progress",
            "data": {"scan_id": job.scan_id, "progress": percent},
        })

    try:
        scan_id, filepath = await scan_service.scan(
            resolution=request.resolution,
            mode=request.mode,
            fmt=request.format,
            source=request.source,
            progress_callback=progress_callback,
        )

        job.scan_id = scan_id
        job.filepath = filepath
        job.file_size = os.path.getsize(filepath)
        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(job)

        await ws_manager.broadcast(f"scan:{job.scan_id}", {
            "type": "scan_completed",
            "data": {"scan_id": job.scan_id},
        })

    except ScanError as e:
        job.status = "failed"
        job.error_message = str(e)
        await db.commit()
        raise HTTPException(status_code=500, detail=str(e))

    return job


@router.post("/scan/batch", response_model=ScanResponse, status_code=201)
async def initiate_batch_scan(
    request: ScanBatchRequest,
    user: User = Depends(require_permission("scan")),
    db: AsyncSession = Depends(get_db),
):
    """Initiate a multi-page ADF batch scan into a single PDF."""
    job = ScanJob(
        user_id=user.id,
        resolution=request.resolution,
        mode=request.mode,
        format="pdf",
        source="ADF",
        status="scanning",
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    async def progress_callback(scan_id: str, percent: float):
        await ws_manager.broadcast(f"scan:{job.scan_id}", {
            "type": "scan_progress",
            "data": {"scan_id": job.scan_id, "progress": percent},
        })

    try:
        scan_id, filepath, page_count = await scan_service.scan_batch(
            resolution=request.resolution,
            mode=request.mode,
            progress_callback=progress_callback,
        )

        job.scan_id = scan_id
        job.filepath = filepath
        job.file_size = os.path.getsize(filepath)
        job.page_count = page_count
        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(job)

        await ws_manager.broadcast(f"scan:{job.scan_id}", {
            "type": "scan_completed",
            "data": {"scan_id": job.scan_id, "page_count": page_count},
        })

    except ScanError as e:
        job.status = "failed"
        job.error_message = str(e)
        await db.commit()
        raise HTTPException(status_code=500, detail=str(e))

    return job


@router.get("/scans", response_model=ScanList)
async def list_scans(
    limit: int = 50,
    offset: int = 0,
    user: User = Depends(require_permission("scan")),
    db: AsyncSession = Depends(get_db),
):
    """List recent scans."""
    count_result = await db.execute(select(func.count(ScanJob.id)))
    total = count_result.scalar() or 0

    result = await db.execute(
        select(ScanJob).order_by(ScanJob.created_at.desc()).limit(limit).offset(offset)
    )
    scans = result.scalars().all()
    return ScanList(scans=scans, total=total)


@router.get("/scans/{scan_id}/download")
async def download_scan(
    scan_id: str,
    user: User = Depends(require_permission("scan")),
    db: AsyncSession = Depends(get_db),
):
    """Download a completed scan file."""
    result = await db.execute(select(ScanJob).where(ScanJob.scan_id == scan_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    if job.status != "completed" or not job.filepath:
        raise HTTPException(status_code=400, detail="Scan is not available for download")
    if not os.path.exists(job.filepath):
        raise HTTPException(status_code=404, detail="Scan file not found on disk")

    return FileResponse(
        job.filepath,
        filename=f"scan_{scan_id}.{job.format}",
        media_type=f"application/{job.format}" if job.format == "pdf" else f"image/{job.format}",
    )


@router.delete("/scans/{scan_id}", status_code=204)
async def delete_scan(
    scan_id: str,
    user: User = Depends(require_permission("scan")),
    db: AsyncSession = Depends(get_db),
):
    """Delete a scan and its file."""
    result = await db.execute(select(ScanJob).where(ScanJob.scan_id == scan_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Scan not found")

    if job.filepath:
        cleanup_file(job.filepath)

    await db.delete(job)
    await db.commit()


# WebSocket endpoint for scan progress
@router.websocket("/ws/scan/{scan_id}")
async def scan_progress_ws(websocket: WebSocket, scan_id: str):
    """WebSocket for real-time scan progress updates."""
    channel = f"scan:{scan_id}"
    await ws_manager.connect(channel, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(channel, websocket)
