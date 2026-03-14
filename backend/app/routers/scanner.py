import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_permission
from app.config import settings
from app.database import get_db
from app.models import CloudProvider, ScanJob, ScanProfile, SMBShare, User
from app.schemas import BulkDeleteScansRequest, BulkDeleteResponse, CollateRequest, EmailSendRequest, ScanBatchRequest, ScanList, ScanProfileCreate, ScanProfileResponse, ScanRequest, ScanResponse
from app.services.cloud_service import CloudError, cloud_service
from app.services.email_service import EmailError, email_service
from app.services.file_service import cleanup_file
from app.services.audit_service import log_event
from app.routers.scanners import _ensure_airscan_config
from app.services.scan_service import ScanError, scan_service, get_default_scanner, get_default_scanner_device, run_post_scan_actions
from app.services.smb_service import SMBError, smb_service
from app.services.webhook_service import dispatch_webhook
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

    # Ensure airscan.conf has this device (self-healing after container rebuild)
    if scanner and device and device.startswith("airscan:"):
        _ensure_airscan_config(scanner.name, device, scanner.post_scan_config)

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

        await log_event(db, "scan.complete", "scan_job", job.scan_id, user_id=user.id,
                        detail={"format": request.format, "resolution": request.resolution})
        await dispatch_webhook(db, "scan.complete", {"scan_id": job.scan_id, "format": request.format})

        if scanner and scanner.auto_deliver:
            await run_post_scan_actions(job, scanner, db)

    except ScanError as e:
        job.status = "failed"
        job.error_message = str(e)
        try:
            await db.commit()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        job.status = "failed"
        job.error_message = f"{type(e).__name__}: {e}"
        try:
            await db.commit()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")

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

        await log_event(db, "scan.complete", "scan_job", job.scan_id, user_id=user.id,
                        detail={"format": request.format, "pages": page_count})
        await dispatch_webhook(db, "scan.complete", {"scan_id": job.scan_id, "format": request.format, "pages": page_count})

        if scanner and scanner.auto_deliver:
            await run_post_scan_actions(job, scanner, db)

    except ScanError as e:
        job.status = "failed"
        job.error_message = str(e)
        try:
            await db.commit()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        job.status = "failed"
        job.error_message = f"{type(e).__name__}: {e}"
        try:
            await db.commit()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")

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
        elif provider.provider == "onedrive":
            file_id = await cloud_service.upload_to_onedrive(
                filepath=job.filepath,
                filename=filename,
                access_token_encrypted=provider.access_token_encrypted,
            )
            return {"message": "Uploaded to OneDrive", "file_id": file_id}
        else:
            raise HTTPException(status_code=400, detail=f"Unknown provider: {provider.provider}")
    except CloudError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/scans/{scan_id}/paperless")
async def send_scan_to_paperless(
    scan_id: str,
    user: User = Depends(require_permission("scan")),
    db: AsyncSession = Depends(get_db),
):
    """Send a scan to Paperless-ngx for archival."""
    result = await db.execute(select(ScanJob).where(ScanJob.scan_id == scan_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    if job.status != "completed" or not job.filepath:
        raise HTTPException(status_code=400, detail="Scan is not available")

    from app.routers.settings import _load_db_values, _db_key
    from app.services.paperless_service import PaperlessError, paperless_service
    from app.services.crypto import encrypt_value

    db_values = await _load_db_values(db)

    paperless_url = db_values.get("paperless_url") or ""
    if not paperless_url:
        from app.config import settings as app_settings
        paperless_url = app_settings.paperless_url

    api_token_key = _db_key("paperless_api_token", True)
    api_token_encrypted = db_values.get(api_token_key) or ""
    if not api_token_encrypted:
        from app.config import settings as app_settings
        if app_settings.paperless_api_token:
            api_token_encrypted = encrypt_value(app_settings.paperless_api_token)

    if not paperless_url or not api_token_encrypted:
        raise HTTPException(status_code=400, detail="Paperless-ngx not configured")

    filename = f"scan_{scan_id}.{job.format}"

    try:
        task_id = await paperless_service.push_document(
            filepath=job.filepath,
            filename=filename,
            paperless_url=paperless_url,
            api_token_encrypted=api_token_encrypted,
            title=filename,
        )
        return {"message": "Sent to Paperless-ngx", "task_id": task_id}
    except PaperlessError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/scans/{scan_id}/ocr")
async def apply_ocr_to_scan(
    scan_id: str,
    user: User = Depends(require_permission("scan")),
    db: AsyncSession = Depends(get_db),
):
    """Apply OCR to a completed PDF scan, making it searchable."""
    result = await db.execute(select(ScanJob).where(ScanJob.scan_id == scan_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    if job.status != "completed" or not job.filepath:
        raise HTTPException(status_code=400, detail="Scan is not available")
    if job.format != "pdf":
        raise HTTPException(status_code=400, detail="OCR is only supported for PDF scans")

    from app.services.ocr_service import OCRError, ocr_service
    from app.routers.settings import _load_db_values

    db_values = await _load_db_values(db)
    language = db_values.get("ocr_language") or settings.ocr_language

    try:
        await ocr_service.apply_ocr(job.filepath, language=language)
        # Update file size after OCR
        job.file_size = os.path.getsize(job.filepath)
        await db.commit()
        return {"message": "OCR applied successfully"}
    except OCRError as e:
        raise HTTPException(status_code=500, detail=str(e))


class EnhanceRequest(BaseModel):
    brightness: float = Field(default=1.0, ge=0.1, le=3.0)
    contrast: float = Field(default=1.0, ge=0.1, le=3.0)
    rotation: int = Field(default=0)
    auto_crop: bool = False
    deskew: bool = False


@router.post("/scans/{scan_id}/enhance")
async def enhance_scan(
    scan_id: str,
    body: EnhanceRequest,
    user: User = Depends(require_permission("scan")),
    db: AsyncSession = Depends(get_db),
):
    """Apply image enhancements (brightness, contrast, rotation, crop) to a completed scan."""
    result = await db.execute(select(ScanJob).where(ScanJob.scan_id == scan_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    if job.status != "completed" or not job.filepath:
        raise HTTPException(status_code=400, detail="Scan is not available")
    if job.format == "pdf":
        raise HTTPException(status_code=400, detail="Image enhancement is for image scans (png/jpeg/tiff), not PDFs")

    from app.services.image_service import ImageError, image_service
    try:
        await image_service.enhance(
            job.filepath,
            brightness=body.brightness,
            contrast=body.contrast,
            rotation=body.rotation,
            auto_crop=body.auto_crop,
            deskew=body.deskew,
        )
        job.file_size = os.path.getsize(job.filepath)
        await db.commit()
        return {"message": "Enhancement applied"}
    except ImageError as e:
        raise HTTPException(status_code=500, detail=str(e))


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


@router.post("/scans/bulk-delete", response_model=BulkDeleteResponse)
async def bulk_delete_scans(
    body: BulkDeleteScansRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete multiple scans and their files."""
    result = await db.execute(select(ScanJob).where(ScanJob.scan_id.in_(body.scan_ids)))
    jobs = result.scalars().all()

    deleted = 0
    for job in jobs:
        if job.filepath:
            cleanup_file(job.filepath)
        await db.delete(job)
        deleted += 1

    await db.commit()

    for scan_id in body.scan_ids:
        await ws_manager.broadcast("scans", {
            "type": "scan_deleted", "data": {"scan_id": scan_id}
        })

    return BulkDeleteResponse(deleted=deleted)


@router.post("/collate", response_model=ScanResponse, status_code=201)
async def collate_scans(
    body: CollateRequest,
    user: User = Depends(require_permission("scan")),
    db: AsyncSession = Depends(get_db),
):
    """Convert or merge scans into a single PDF."""
    import uuid as _uuid

    from PIL import Image
    from pypdf import PdfWriter

    result = await db.execute(select(ScanJob).where(ScanJob.scan_id.in_(body.scan_ids)))
    jobs_map = {j.scan_id: j for j in result.scalars().all()}

    # Preserve order from request
    ordered_jobs = []
    for sid in body.scan_ids:
        job = jobs_map.get(sid)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Scan {sid} not found")
        if job.status != "completed" or not job.filepath:
            raise HTTPException(status_code=400, detail=f"Scan {sid} is not available")
        if not os.path.exists(job.filepath):
            raise HTTPException(status_code=404, detail=f"File for scan {sid} not found on disk")
        ordered_jobs.append(job)

    scan_id = str(_uuid.uuid4())
    out_path = os.path.join(settings.scan_dir, f"{scan_id}.pdf")
    writer = PdfWriter()

    for job in ordered_jobs:
        ext = os.path.splitext(job.filepath)[1].lower()
        if ext == ".pdf":
            writer.append(job.filepath)
        else:
            # Convert image to single-page PDF in memory
            img = Image.open(job.filepath)
            if img.mode not in ("RGB", "L", "RGBA"):
                img = img.convert("RGB")
            tmp_pdf = job.filepath + ".tmp.pdf"
            img.save(tmp_pdf, format="PDF", resolution=job.resolution)
            img.close()
            writer.append(tmp_pdf)
            os.unlink(tmp_pdf)

    with open(out_path, "wb") as f:
        writer.write(f)
    writer.close()

    merged_job = ScanJob(
        user_id=user.id,
        scan_id=scan_id,
        resolution=ordered_jobs[0].resolution,
        mode=ordered_jobs[0].mode,
        format="pdf",
        source="Merged",
        page_count=sum(j.page_count for j in ordered_jobs),
        filepath=out_path,
        file_size=os.path.getsize(out_path),
        status="completed",
        completed_at=datetime.now(timezone.utc),
    )
    db.add(merged_job)
    await db.commit()
    await db.refresh(merged_job)

    await ws_manager.broadcast("scans", {
        "type": "scan_completed", "data": {"scan_id": scan_id}
    })

    return merged_job


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

    await log_event(db, "scan.delete", "scan_job", scan_id_copy, user_id=user.id)
    await dispatch_webhook(db, "scan.delete", {"scan_id": scan_id_copy})

    await ws_manager.broadcast("scans", {
        "type": "scan_deleted", "data": {"scan_id": scan_id_copy}
    })


# --- Scan Profiles ---


@router.get("/profiles", response_model=list[ScanProfileResponse])
async def list_profiles(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List the current user's scan profiles."""
    result = await db.execute(
        select(ScanProfile).where(ScanProfile.user_id == user.id).order_by(ScanProfile.name)
    )
    return result.scalars().all()


@router.post("/profiles", response_model=ScanProfileResponse, status_code=201)
async def create_profile(
    data: ScanProfileCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a scan profile."""
    profile = ScanProfile(
        name=data.name,
        resolution=data.resolution,
        color_mode=data.color_mode,
        format=data.format,
        source=data.source,
        ocr_enabled=data.ocr_enabled,
        user_id=user.id,
    )
    db.add(profile)
    await db.commit()
    await db.refresh(profile)
    return profile


@router.put("/profiles/{profile_id}", response_model=ScanProfileResponse)
async def update_profile(
    profile_id: int,
    data: ScanProfileCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a scan profile."""
    result = await db.execute(
        select(ScanProfile).where(ScanProfile.id == profile_id, ScanProfile.user_id == user.id)
    )
    profile = result.scalar_one_or_none()
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")

    profile.name = data.name
    profile.resolution = data.resolution
    profile.color_mode = data.color_mode
    profile.format = data.format
    profile.source = data.source
    profile.ocr_enabled = data.ocr_enabled
    await db.commit()
    await db.refresh(profile)
    return profile


@router.delete("/profiles/{profile_id}", status_code=204)
async def delete_profile(
    profile_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a scan profile."""
    result = await db.execute(
        select(ScanProfile).where(ScanProfile.id == profile_id, ScanProfile.user_id == user.id)
    )
    profile = result.scalar_one_or_none()
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")

    await db.delete(profile)
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
