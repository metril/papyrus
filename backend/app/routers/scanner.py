import asyncio
import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_permission
from app.auth.ws import authenticate_websocket, has_ws_permission
from app.database import async_session, get_db
from app.models import CloudProvider, ScanJob, ScanProfile, SMBShare, User
from app.schemas import (
    BulkDeleteResponse,
    BulkDeleteScansRequest,
    CollateRequest,
    EmailSendRequest,
    ScanBatchRequest,
    ScanList,
    ScanProfileCreate,
    ScanProfileResponse,
    ScanRequest,
    ScanResponse,
    serialize_scan_job,
)
from app.services.audit_service import log_event
from app.services.cloud_service import cloud_service
from app.services.email_service import email_service
from app.services.file_service import cleanup_file, sanitize_filename
from app.services.scan_service import (
    ScanError,
    get_default_scanner,
    get_default_scanner_device,
    run_post_scan_actions,
    scan_service,
)
from app.services.smb_service import smb_service
from app.services.thumbnail_service import (
    THUMBNAIL_CACHE_CONTROL,
    get_or_create_thumbnail,
    invalidate_thumbnail,
)
from app.services.webhook_service import dispatch_webhook
from app.services.ws_manager import ws_manager

router = APIRouter()


@router.get("/status")
async def get_scanner_status(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Check if the scanner device is available.

    F42: resolves the configured device from the DB first -- previously this
    never called configure(), so the singleton's device stayed "" and
    check_device's old ``"" in output`` check reported every device as
    available unconditionally.
    """
    scanner = await get_default_scanner(db)
    device = scanner.device if scanner else await get_default_scanner_device(db)
    return await scan_service.check_device(device=device)


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
    from app.routers.settings import get_setting
    scanner = await get_default_scanner(db)
    device = scanner.device if scanner else await get_default_scanner_device(db)

    # F43: resolved fresh per request and passed straight into scan()/
    # run_post_scan_actions instead of mutating the shared scan_service
    # singleton -- a concurrent request's configure() used to be able to
    # change another in-flight scan's output directory/filename template
    # mid-run.
    scan_dir = await get_setting(db, "scan_dir") or "/app/data/scans"
    filename_template = (
        await get_setting(db, "scan_filename_template") or "scan_{date}_{time}_{id}"
    )

    # F26: honor a client-supplied scan_id (so it can open the progress
    # WebSocket before POSTing) or generate one -- either way this is the id
    # used for the DB row *and* every progress broadcast, so the two can
    # never disagree.
    scan_job_id = str(request.scan_id) if request.scan_id else str(uuid.uuid4())

    # Create scan job record
    job = ScanJob(
        user_id=user.id,
        scan_id=scan_job_id,
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

    async def progress_callback(_scan_id: str, percent: float):
        await ws_manager.broadcast(f"scan:{job.scan_id}", {
            "type": "scan_progress",
            "data": {"scan_id": job.scan_id, "progress": percent},
        })

    try:
        _, filepath = await scan_service.scan(
            resolution=request.resolution,
            mode=request.mode,
            fmt=request.format,
            source=request.source,
            progress_callback=progress_callback,
            device=device,
            scan_dir=scan_dir,
            filename_template=filename_template,
        )

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
            "data": serialize_scan_job(job),
        })

        await log_event(db, "scan.complete", "scan_job", job.scan_id, user_id=user.id,
                        detail={"format": request.format, "resolution": request.resolution})
        await db.commit()
        await dispatch_webhook(
            db, "scan.complete", {"scan_id": job.scan_id, "format": request.format}
        )

        if scanner and scanner.auto_deliver:
            await run_post_scan_actions(
                job, scanner, db, default_filename_template=filename_template
            )

    except ScanError as e:
        job.status = "failed"
        job.error_message = str(e)
        try:
            await db.commit()
        except Exception:
            pass
        # ScanError is a PapyrusError (502) — let the global handler render it.
        raise
    except Exception as e:
        job.status = "failed"
        job.error_message = f"{type(e).__name__}: {e}"
        try:
            await db.commit()
        except Exception:
            pass
        # Never leak the raw exception text: the catch-all handler logs the
        # traceback and returns a generic 500.
        raise

    return job


@router.post("/scan/batch", response_model=ScanResponse, status_code=201)
async def initiate_batch_scan(
    request: ScanBatchRequest,
    user: User = Depends(require_permission("scan")),
    db: AsyncSession = Depends(get_db),
):
    """Initiate a multi-page ADF batch scan into a single PDF."""
    from app.routers.settings import get_setting
    scanner = await get_default_scanner(db)
    device = scanner.device if scanner else await get_default_scanner_device(db)

    # F43: see initiate_scan's identical comment.
    scan_dir = await get_setting(db, "scan_dir") or "/app/data/scans"
    filename_template = (
        await get_setting(db, "scan_filename_template") or "scan_{date}_{time}_{id}"
    )

    # F26: see initiate_scan's identical comment.
    scan_job_id = str(request.scan_id) if request.scan_id else str(uuid.uuid4())

    job = ScanJob(
        user_id=user.id,
        scan_id=scan_job_id,
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

    async def progress_callback(_scan_id: str, percent: float):
        await ws_manager.broadcast(f"scan:{job.scan_id}", {
            "type": "scan_progress",
            "data": {"scan_id": job.scan_id, "progress": percent},
        })

    try:
        _, filepath, page_count = await scan_service.scan_batch(
            resolution=request.resolution,
            mode=request.mode,
            progress_callback=progress_callback,
            device=device,
            scan_dir=scan_dir,
        )

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
            "data": serialize_scan_job(job),
        })

        await log_event(db, "scan.complete", "scan_job", job.scan_id, user_id=user.id,
                        detail={"format": request.format, "pages": page_count})
        await db.commit()
        await dispatch_webhook(
            db, "scan.complete",
            {"scan_id": job.scan_id, "format": request.format, "pages": page_count},
        )

        if scanner and scanner.auto_deliver:
            await run_post_scan_actions(
                job, scanner, db, default_filename_template=filename_template
            )

    except ScanError as e:
        job.status = "failed"
        job.error_message = str(e)
        try:
            await db.commit()
        except Exception:
            pass
        # ScanError is a PapyrusError (502) — let the global handler render it.
        raise
    except Exception as e:
        job.status = "failed"
        job.error_message = f"{type(e).__name__}: {e}"
        try:
            await db.commit()
        except Exception:
            pass
        # Never leak the raw exception text: the catch-all handler logs the
        # traceback and returns a generic 500.
        raise

    return job


@router.get("/scans", response_model=ScanList)
async def list_scans(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
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


@router.get("/scans/{scan_id}/thumbnail")
async def get_scan_thumbnail(
    scan_id: str,
    user: User = Depends(require_permission("scan")),
    db: AsyncSession = Depends(get_db),
):
    """Return a small cached preview (~320px) of a completed scan.

    Generated on first request and reused after; much cheaper to load in
    list/grid views than the full scan file.
    """
    result = await db.execute(select(ScanJob).where(ScanJob.scan_id == scan_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    if job.status != "completed" or not job.filepath:
        raise HTTPException(status_code=404, detail="Scan is not available")
    if not os.path.exists(job.filepath):
        raise HTTPException(status_code=404, detail="Scan file not found on disk")

    try:
        thumb_path = await get_or_create_thumbnail(job.filepath)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Scan file not found on disk")

    return FileResponse(
        thumb_path,
        media_type="image/jpeg",
        content_disposition_type="inline",
        headers={"Cache-Control": THUMBNAIL_CACHE_CONTROL},
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

    await email_service.send_scan(
        to=data.to,
        subject=data.subject,
        body=data.body,
        filepath=job.filepath,
        filename=f"scan_{scan_id}.{job.format}",
        db_config=db_config,
    )

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

    # F14: refresh the token first if it's expired, rather than handing the
    # (possibly stale) stored token straight to the provider SDK.
    if provider.provider not in ("gdrive", "dropbox", "onedrive"):
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider.provider}")
    access_token = await cloud_service.get_valid_access_token(db, provider)

    if provider.provider == "gdrive":
        file_id = await cloud_service.upload_to_gdrive(
            filepath=job.filepath,
            filename=filename,
            access_token=access_token,
        )
        return {"message": "Uploaded to Google Drive", "file_id": file_id}
    elif provider.provider == "dropbox":
        path = await cloud_service.upload_to_dropbox(
            filepath=job.filepath,
            filename=filename,
            access_token=access_token,
        )
        return {"message": "Uploaded to Dropbox", "path": path}
    else:
        file_id = await cloud_service.upload_to_onedrive(
            filepath=job.filepath,
            filename=filename,
            access_token=access_token,
        )
        return {"message": "Uploaded to OneDrive", "file_id": file_id}


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

    from app.routers.settings import get_setting
    from app.services.crypto import encrypt_value
    from app.services.paperless_service import paperless_service
    paperless_url = await get_setting(db, "paperless_url") or ""
    api_token = await get_setting(db, "paperless_api_token") or ""
    api_token_encrypted = encrypt_value(api_token) if api_token else ""

    if not paperless_url or not api_token_encrypted:
        raise HTTPException(status_code=400, detail="Paperless-ngx not configured")

    filename = f"scan_{scan_id}.{job.format}"

    task_id = await paperless_service.push_document(
        filepath=job.filepath,
        filename=filename,
        paperless_url=paperless_url,
        api_token_encrypted=api_token_encrypted,
        title=filename,
    )
    return {"message": "Sent to Paperless-ngx", "task_id": task_id}


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

    from app.routers.settings import get_setting
    from app.services.ocr_service import ocr_service
    language = await get_setting(db, "ocr_language") or "eng"

    await ocr_service.apply_ocr(job.filepath, language=language)
    # Update file size after OCR
    job.file_size = os.path.getsize(job.filepath)
    await db.commit()
    # The file was rewritten in place; drop any cached thumbnail so the
    # next request regenerates it instead of serving a stale preview.
    invalidate_thumbnail(job.filepath)
    return {"message": "OCR applied successfully"}


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
        raise HTTPException(
            status_code=400,
            detail="Image enhancement is for image scans (png/jpeg/tiff), not PDFs",
        )

    from app.services.image_service import image_service
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
    # The file was rewritten in place; drop any cached thumbnail so the
    # next request regenerates it instead of serving a stale preview.
    invalidate_thumbnail(job.filepath)
    return {"message": "Enhancement applied"}


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

    await smb_service.upload(
        server=share.server,
        share_name=share.share_name,
        remote_path=dest_path,
        local_path=job.filepath,
        username=share.username,
        password_encrypted=share.password_encrypted,
        domain=share.domain,
    )

    return {"message": f"Scan saved to {share.name}:{dest_path}"}


@router.post("/scans/bulk-delete", response_model=BulkDeleteResponse)
async def bulk_delete_scans(
    body: BulkDeleteScansRequest,
    user: User = Depends(require_permission("scan")),
    db: AsyncSession = Depends(get_db),
):
    """Delete multiple scans and their files."""
    result = await db.execute(select(ScanJob).where(ScanJob.scan_id.in_(body.scan_ids)))
    jobs = result.scalars().all()

    deleted = 0
    filepaths: list[str] = []
    for job in jobs:
        if job.filepath:
            filepaths.append(job.filepath)
        await db.delete(job)
        deleted += 1

    # F44: rows are deleted and committed before their files are unlinked, so
    # a slow/failing unlink can never leave a committed row pointing at a
    # file that's already gone.
    await db.commit()

    for filepath in filepaths:
        await asyncio.to_thread(cleanup_file, filepath)

    for scan_id in body.scan_ids:
        await ws_manager.broadcast("scans", {
            "type": "scan_deleted", "data": {"scan_id": scan_id}
        })

    return BulkDeleteResponse(deleted=deleted)


def _collate_pdfs_sync(page_specs: list[tuple[str, int]], out_path: str) -> None:
    """Merge scan files (PDFs or images) into a single PDF at ``out_path``.

    ``page_specs`` is a list of (filepath, resolution) tuples in output order.
    CPU-bound PIL image->PDF conversion + PdfWriter merge — run via
    ``asyncio.to_thread`` so it doesn't block the event loop.

    F46: each non-PDF page's intermediate single-page PDF used to be written
    next to its *source* scan file (``filepath + ".tmp.pdf"``) with no
    per-invocation uniqueness — two concurrent collates sharing a source
    image could unlink the file the other was still reading, and any
    mid-loop failure leaked it permanently. A fresh ``tempfile.mkdtemp()``
    work directory (removed in ``finally``, on success or failure alike)
    isolates every call from every other and from the source files.
    """
    import shutil
    import tempfile

    from PIL import Image
    from pypdf import PdfWriter

    work_dir = tempfile.mkdtemp(prefix="papyrus_collate_")
    try:
        writer = PdfWriter()
        try:
            for i, (filepath, resolution) in enumerate(page_specs):
                ext = os.path.splitext(filepath)[1].lower()
                if ext == ".pdf":
                    writer.append(filepath)
                else:
                    # Convert image to a single-page PDF in the work dir
                    img = Image.open(filepath)
                    if img.mode not in ("RGB", "L", "RGBA"):
                        img = img.convert("RGB")
                    tmp_pdf = os.path.join(work_dir, f"page_{i}.pdf")
                    img.save(tmp_pdf, format="PDF", resolution=resolution)
                    img.close()
                    writer.append(tmp_pdf)

            with open(out_path, "wb") as f:
                writer.write(f)
        finally:
            writer.close()
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


@router.post("/collate", response_model=ScanResponse, status_code=201)
async def collate_scans(
    body: CollateRequest,
    user: User = Depends(require_permission("scan")),
    db: AsyncSession = Depends(get_db),
):
    """Convert or merge scans into a single PDF."""
    import uuid as _uuid

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
    from app.routers.settings import get_setting
    _scan_dir = await get_setting(db, "scan_dir") or "/app/data/scans"

    # F136: honor the requested output_filename for the stored file (it used
    # to be silently ignored -- the merged file was always named `{uuid}.pdf`
    # with no trace the request had asked for something else). Sanitized and
    # scan_id-prefixed so it stays collision-safe and traversal-safe while
    # still reflecting what the client asked for.
    safe_name = sanitize_filename(body.output_filename) or "merged.pdf"
    if not safe_name.lower().endswith(".pdf"):
        safe_name += ".pdf"
    out_path = os.path.join(_scan_dir, f"{scan_id}_{safe_name}")

    page_specs = [(job.filepath, job.resolution) for job in ordered_jobs]
    await asyncio.to_thread(_collate_pdfs_sync, page_specs, out_path)

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
        "type": "scan_completed", "data": serialize_scan_job(merged_job)
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

    filepath = job.filepath
    scan_id_copy = job.scan_id
    await db.delete(job)
    # F44: commit before unlinking, so a slow/failing unlink can never leave
    # a committed row pointing at a file that's already gone.
    await db.commit()

    if filepath:
        await asyncio.to_thread(cleanup_file, filepath)

    await log_event(db, "scan.delete", "scan_job", scan_id_copy, user_id=user.id)
    await db.commit()
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
    """WebSocket for real-time scan progress updates.

    F48: authenticated (and permission-checked -- "scan") before accept(),
    same as the system.py channels -- ScanForm opens this before POSTing
    /scan, using the browser's session cookie, which authenticate_websocket
    honors same as any other route. The auth lookup uses its own
    short-lived session (see system.py's jobs_ws docstring for why -- a
    request-scoped `Depends(get_db)` session would stay checked out of the
    pool for as long as the socket is open).
    """
    async with async_session() as db:
        identity = await authenticate_websocket(websocket, db)
    if identity is None or not has_ws_permission(identity, "scan"):
        await websocket.close(code=1008)
        return
    channel = f"scan:{scan_id}"
    await ws_manager.connect(channel, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(channel, websocket)
