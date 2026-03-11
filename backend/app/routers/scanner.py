import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_permission
from app.database import get_db
from app.models import CloudProvider, ScanJob, SMBShare, User
from app.schemas import EmailSendRequest, ScanBatchRequest, ScanList, ScanRequest, ScanResponse
from app.services.cloud_service import CloudError, cloud_service
from app.services.email_service import EmailError, email_service
from app.services.file_service import cleanup_file
from app.services.scan_service import ScanError, scan_service, get_default_scanner, get_default_scanner_device, run_post_scan_actions
from app.services.smb_service import SMBError, smb_service
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
    scanner = await get_default_scanner(db)
    device = scanner.device if scanner else await get_default_scanner_device(db)

    # Create scan job record
    job = ScanJob(
        user_id=user.id,
        resolution=request.resolution,
        mode=request.mode,
        format=request.format,
        source=request.source,
        status="scanning",
        scanner_id=scanner.id if scanner else None,
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
            device=device,
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
        await ws_manager.broadcast("scans", {
            "type": "scan_completed",
            "data": {"scan_id": job.scan_id},
        })

        if scanner and scanner.auto_deliver:
            await run_post_scan_actions(job, scanner, db)

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
    scanner = await get_default_scanner(db)
    device = scanner.device if scanner else await get_default_scanner_device(db)

    job = ScanJob(
        user_id=user.id,
        resolution=request.resolution,
        mode=request.mode,
        format="pdf",
        source="ADF",
        status="scanning",
        scanner_id=scanner.id if scanner else None,
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
            device=device,
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
        await ws_manager.broadcast("scans", {
            "type": "scan_completed",
            "data": {"scan_id": job.scan_id},
        })

        if scanner and scanner.auto_deliver:
            await run_post_scan_actions(job, scanner, db)

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
        content_disposition_type="inline",
    )


@router.post("/scans/{scan_id}/email")
async def email_scan(
    scan_id: str,
    data: EmailSendRequest,
    user: User = Depends(require_permission("scan")),
    db: AsyncSession = Depends(get_db),
):
    """Email a scan as an attachment."""
    result = await db.execute(select(ScanJob).where(ScanJob.scan_id == scan_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    if job.status != "completed" or not job.filepath:
        raise HTTPException(status_code=400, detail="Scan is not available")

    # Load SMTP config from DB
    from app.routers.email import _get_smtp_config
    db_config = await _get_smtp_config(db)

    try:
        await email_service.send_scan(
            to=data.to,
            subject=data.subject,
            body=data.body,
            filepath=job.filepath,
            filename=f"scan_{scan_id}.{job.format}",
            db_config=db_config,
        )
    except EmailError as e:
        raise HTTPException(status_code=502, detail=str(e))

    return {"message": f"Scan emailed to {data.to}"}


@router.post("/scans/{scan_id}/cloud")
async def upload_scan_to_cloud(
    scan_id: str,
    provider_id: int,
    user: User = Depends(require_permission("scan")),
    db: AsyncSession = Depends(get_db),
):
    """Upload a scan to a connected cloud storage provider."""
    result = await db.execute(select(ScanJob).where(ScanJob.scan_id == scan_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    if job.status != "completed" or not job.filepath:
        raise HTTPException(status_code=400, detail="Scan is not available")

    # Get cloud provider
    provider_result = await db.execute(
        select(CloudProvider).where(
            CloudProvider.id == provider_id,
            CloudProvider.user_id == user.id,
        )
    )
    provider = provider_result.scalar_one_or_none()
    if provider is None:
        raise HTTPException(status_code=404, detail="Cloud provider not found")

    filename = f"scan_{scan_id}.{job.format}"

    try:
        if provider.provider == "gdrive":
            file_id = await cloud_service.upload_to_gdrive(
                filepath=job.filepath,
                filename=filename,
                access_token_encrypted=provider.access_token_encrypted,
            )
            return {"message": "Uploaded to Google Drive", "file_id": file_id}
        elif provider.provider == "dropbox":
            path = await cloud_service.upload_to_dropbox(
                filepath=job.filepath,
                filename=filename,
                access_token_encrypted=provider.access_token_encrypted,
            )
            return {"message": "Uploaded to Dropbox", "path": path}
        else:
            raise HTTPException(status_code=400, detail=f"Unknown provider: {provider.provider}")
    except CloudError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/scans/{scan_id}/smb")
async def save_scan_to_smb(
    scan_id: str,
    share_id: int,
    remote_path: str = "/",
    user: User = Depends(require_permission("scan")),
    db: AsyncSession = Depends(get_db),
):
    """Save a scan to an SMB network share."""
    result = await db.execute(select(ScanJob).where(ScanJob.scan_id == scan_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    if job.status != "completed" or not job.filepath:
        raise HTTPException(status_code=400, detail="Scan is not available")

    share_result = await db.execute(select(SMBShare).where(SMBShare.id == share_id))
    share = share_result.scalar_one_or_none()
    if share is None:
        raise HTTPException(status_code=404, detail="Share not found")

    filename = f"scan_{scan_id}.{job.format}"
    dest_path = f"{remote_path.rstrip('/')}/{filename}"

    try:
        smb_service.upload(
            server=share.server,
            share_name=share.share_name,
            remote_path=dest_path,
            local_path=job.filepath,
            username=share.username,
            password_encrypted=share.password_encrypted,
            domain=share.domain,
        )
    except SMBError as e:
        raise HTTPException(status_code=502, detail=str(e))

    return {"message": f"Scan saved to {share.name}:{dest_path}"}


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

    scan_id_copy = job.scan_id
    await db.delete(job)
    await db.commit()

    await ws_manager.broadcast("scans", {
        "type": "scan_deleted", "data": {"scan_id": scan_id_copy}
    })


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
