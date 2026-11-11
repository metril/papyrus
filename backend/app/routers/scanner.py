import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, Response
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_docs import (
    AUTH_ERRORS,
    BAD_GATEWAY,
    NOT_FOUND,
    VALIDATION,
    error_group,
    file_response,
    std_responses,
)
from app.auth.dependencies import get_current_user, require_permission
from app.auth.ws import authenticate_websocket, has_ws_permission
from app.database import async_session, get_db
from app.exceptions import (
    BacksMismatchError,
    BacksNotEligibleError,
    NotFoundError,
    PapyrusError,
    user_message,
)
from app.models import CloudProvider, ScanJob, Scanner, ScanProfile, SMBShare, User
from app.schemas import (
    BulkDeleteResponse,
    BulkDeleteScansRequest,
    CollateRequest,
    EmailSendRequest,
    Page,
    ScanBatchRequest,
    ScanProfileCreate,
    ScanProfileResponse,
    ScanRequest,
    ScanResponse,
    serialize_scan_job,
)
from app.schemas.scans import (
    BacksRequest,
    BacksResponse,
    EnhanceRequest,
    MessageResponse,
    ScanCloudUploadResponse,
    ScannerOptions,
    ScannerStatus,
    ScanPaperlessResponse,
)
from app.services import duplex_service
from app.services.audit_service import log_event
from app.services.cloud_service import cloud_service
from app.services.email_service import email_service
from app.services.file_locks import lock_for
from app.services.file_service import cleanup_file, sanitize_filename
from app.services.scan_service import (
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

logger = logging.getLogger(__name__)
router = APIRouter()



def _scan_access(user: User, perms: list[str] | None, job: ScanJob) -> bool:
    """Visibility rule for a scan. `perms` is the token's permission list, None for a session."""
    if job.user_id is None:
        return perms is None  # network (eSCL) scans: sessions only, never API tokens
    if job.user_id == user.id:
        return True
    return user.role == "admin" and (perms is None or "admin" in perms)


def can_access_scan(user: User, request: Request, job: ScanJob) -> bool:
    """Whether the caller may see/act on `job`: its owner, an admin (session, or token with the
    `admin` permission), or any session user for a null-owner network scan (API tokens never see
    those)."""
    return _scan_access(user, getattr(request.state, "token_permissions", None), job)


def _scan_visibility(user: User, request: Request):
    """SQL clause equivalent of `can_access_scan`, or None when everything is visible."""
    perms = getattr(request.state, "token_permissions", None)
    if perms is None:
        if user.role == "admin":
            return None
        return or_(ScanJob.user_id == user.id, ScanJob.user_id.is_(None))
    if user.role == "admin" and "admin" in perms:
        return ScanJob.user_id.is_not(None)
    return ScanJob.user_id == user.id


async def _get_accessible_scan(
    db: AsyncSession, request: Request, user: User, scan_id: str
) -> ScanJob:
    """Load a scan by id; unknown and inaccessible both raise the same 404."""
    result = await db.execute(select(ScanJob).where(ScanJob.scan_id == scan_id))
    job = result.scalar_one_or_none()
    if job is None or not can_access_scan(user, request, job):
        raise NotFoundError("Scan not found")
    return job


BAD_REQUEST = error_group(400, "The scan is not available (not completed) or the request does not "
    "fit it.")
SOURCE_CONFLICT = error_group(409, "Wrong paper source state, e.g. an empty feeder or a back-side "
    "page-count mismatch.")
SCANNER_BUSY = error_group(503, "The scanner is busy or unreachable.")


@router.get(
    "/scanners/default/status",
    summary="Check scanner availability",
    response_model=ScannerStatus,
    response_model_exclude_unset=True,
    responses=std_responses(AUTH_ERRORS, VALIDATION, SCANNER_BUSY),
)
async def get_scanner_status(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Report whether the default scanner is reachable.

    Any authenticated user. Uses the default scanner's device (or the configured fallback). Native
    `brothernet:` devices report `busy: true` while a scan is running and still count as available.
    """
    scanner = await get_default_scanner(db)
    device = scanner.device if scanner else await get_default_scanner_device(db)
    return await scan_service.check_device(device=device)


@router.get(
    "/scanners/default/options",
    summary="Get scanner options",
    response_model=ScannerOptions,
    responses=std_responses(AUTH_ERRORS, VALIDATION),
)
async def get_scanner_options(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List the resolutions, colour modes, formats and sources the default scanner supports.

    Any authenticated user. `feeder_formats` lists the formats usable with the feeder (PDF only on
    Brother devices, whose feeder always scans the whole stack).
    """
    scanner = await get_default_scanner(db)
    return await scan_service.get_options(scanner.device if scanner else None)


@router.post(
    "/scans",
    summary="Start a scan",
    response_model=ScanResponse,
    status_code=201,
    responses=std_responses(AUTH_ERRORS, VALIDATION, SOURCE_CONFLICT, SCANNER_BUSY),
)
async def initiate_scan(
    request: ScanRequest,
    user: User = Depends(require_permission("scan")),
    db: AsyncSession = Depends(get_db),
):
    """Scan one page (or the whole stack) and store the result.

    Requires the `scan` permission. Runs synchronously: progress is broadcast on
    `/ws/scans/{scan_id}` and the finished row on the `scans` channel (`scan_completed`); fires the
    `scan.complete` webhook and post-scan actions. Pass `scan_id` to subscribe to progress before
    posting.

    Feeder/glass: on Brother devices paper loaded in the feeder takes over a glass request, so the
    scan can come back as a multi-page PDF with `source=ADF`; check the returned `format`, `source`
    and `page_count`. An explicit feeder request with an empty feeder returns 409. Feeder scans on
    those devices are PDF-only (422 otherwise).
    """
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
    await db.commit()  # refresh autobegins; end it before the long scan

    async def progress_callback(_scan_id: str, percent: float):
        await ws_manager.broadcast(f"scan:{job.scan_id}", {
            "type": "scan_progress",
            "data": {"scan_id": job.scan_id, "progress": percent},
        })

    def set_page_count(count: int) -> None:
        job.page_count = count

    def set_fed_by_feeder() -> None:
        # Paper in the feeder took over a glass request: one PDF of every sheet.
        job.source = "ADF"
        job.format = "pdf"

    try:
        _, filepath = await scan_service.scan(
            page_count_callback=set_page_count,
            feeder_callback=set_fed_by_feeder,
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
                        detail={"format": job.format, "resolution": request.resolution})
        await db.commit()
        await dispatch_webhook(
            db, "scan.complete", {"scan_id": job.scan_id, "format": job.format}
        )
        await db.commit()  # dispatch_webhook's query must not span delivery I/O

        if scanner and scanner.auto_deliver:
            await run_post_scan_actions(
                job, scanner, db, default_filename_template=filename_template
            )

    except PapyrusError as e:
        job.status = "failed"
        job.error_message = str(e)
        try:
            await db.commit()
        except Exception:
            pass
        # PapyrusError carries its own status code — let the global handler render it.
        raise
    except Exception as e:
        job.status = "failed"
        logger.exception("Scan failed")
        job.error_message = user_message(e)
        try:
            await db.commit()
        except Exception:
            pass
        # Never leak the raw exception text: the catch-all handler logs the
        # traceback and returns a generic 500.
        raise

    return job


@router.post(
    "/scans/batch",
    summary="Start a feeder batch scan",
    response_model=ScanResponse,
    status_code=201,
    responses=std_responses(AUTH_ERRORS, VALIDATION, SOURCE_CONFLICT, SCANNER_BUSY),
)
async def initiate_batch_scan(
    request: ScanBatchRequest,
    user: User = Depends(require_permission("scan")),
    db: AsyncSession = Depends(get_db),
):
    """Scan every sheet in the feeder into one multi-page document.

    Requires the `scan` permission. Same progress WebSocket, `scan_completed` broadcast,
    `scan.complete` webhook and post-scan actions as `POST /scans`. Returns 409 when the feeder is
    empty and 503 while the scanner is busy.
    """
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
    await db.commit()  # refresh autobegins; end it before the long scan

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
        await db.commit()  # dispatch_webhook's query must not span delivery I/O

        if scanner and scanner.auto_deliver:
            await run_post_scan_actions(
                job, scanner, db, default_filename_template=filename_template
            )

    except PapyrusError as e:
        job.status = "failed"
        job.error_message = str(e)
        try:
            await db.commit()
        except Exception:
            pass
        # PapyrusError carries its own status code — let the global handler render it.
        raise
    except Exception as e:
        job.status = "failed"
        logger.exception("Scan failed")
        job.error_message = user_message(e)
        try:
            await db.commit()
        except Exception:
            pass
        # Never leak the raw exception text: the catch-all handler logs the
        # traceback and returns a generic 500.
        raise

    return job


@router.get(
    "/scans",
    summary="List scans",
    response_model=Page[ScanResponse],
    responses=std_responses(AUTH_ERRORS, VALIDATION),
)
async def list_scans(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    mine: bool = Query(default=False, description="Only scans created by the caller."),
    user: User = Depends(require_permission("scan")),
    db: AsyncSession = Depends(get_db),
):
    """List scans, newest first, with limit/offset pagination.

    Requires the `scan` permission. Callers see their own scans; admins (session, or a token with
    the `admin` permission) see all; sessions also see network (eSCL) scans, which have no owner.
    With `mine=true` only scans owned by the caller are listed.
    """
    count_q = select(func.count(ScanJob.id))
    list_q = select(ScanJob)
    visible = _scan_visibility(user, request)
    if visible is not None:
        count_q = count_q.where(visible)
        list_q = list_q.where(visible)
    if mine:
        count_q = count_q.where(ScanJob.user_id == user.id)
        list_q = list_q.where(ScanJob.user_id == user.id)
    count_result = await db.execute(count_q)
    total = count_result.scalar() or 0

    result = await db.execute(
        list_q.order_by(ScanJob.created_at.desc()).limit(limit).offset(offset)
    )
    scans = result.scalars().all()
    return Page[ScanResponse](items=scans, total=total, limit=limit, offset=offset)


@router.get(
    "/scans/{scan_id}/download",
    summary="Download a scan file",
    response_class=Response,
    responses=file_response(
        ["application/pdf", "image/png", "image/jpeg", "image/tiff"],
        "The scan file, served inline.",
    )
    | std_responses(AUTH_ERRORS, NOT_FOUND, BAD_REQUEST),
)
async def download_scan(
    scan_id: str,
    request: Request,
    user: User = Depends(require_permission("scan")),
    db: AsyncSession = Depends(get_db),
):
    """Download the file of a completed scan, served inline.

    Requires the `scan` permission. 400 when the scan is not completed; 404 when unknown or the file
    is gone.
    """
    job = await _get_accessible_scan(db, request, user, scan_id)
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


@router.get(
    "/scans/{scan_id}/thumbnail",
    summary="Get a scan thumbnail",
    response_class=Response,
    responses=file_response(["image/jpeg"], "JPEG preview, about 320px.")
    | std_responses(AUTH_ERRORS, NOT_FOUND),
)
async def get_scan_thumbnail(
    scan_id: str,
    request: Request,
    user: User = Depends(require_permission("scan")),
    db: AsyncSession = Depends(get_db),
):
    """Return a small cached JPEG preview (about 320px) of a completed scan.

    Requires the `scan` permission. Generated on first request and cached; much cheaper than the
    full file in list views.
    """
    job = await _get_accessible_scan(db, request, user, scan_id)
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


@router.post(
    "/scans/{scan_id}/email",
    summary="Email a scan",
    response_model=MessageResponse,
    responses=std_responses(AUTH_ERRORS, NOT_FOUND, BAD_REQUEST, VALIDATION, BAD_GATEWAY),
)
async def email_scan(
    scan_id: str,
    request: Request,
    data: EmailSendRequest,
    user: User = Depends(require_permission("scan")),
    db: AsyncSession = Depends(get_db),
):
    """Send a completed scan as an email attachment through the configured SMTP server.

    Requires the `scan` permission.
    """
    job = await _get_accessible_scan(db, request, user, scan_id)
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


def _cloud_path(folder: str) -> str:
    """Normalise a Dropbox/OneDrive folder path to `/a/b` (no `..` segments)."""
    parts = [p for p in folder.split("/") if p]
    if ".." in parts:
        raise HTTPException(status_code=400, detail="Invalid folder path")
    return "/" + "/".join(parts)


@router.post(
    "/scans/{scan_id}/cloud",
    summary="Upload a scan to cloud storage",
    response_model=ScanCloudUploadResponse,
    response_model_exclude_unset=True,
    responses=std_responses(AUTH_ERRORS, NOT_FOUND, BAD_REQUEST, VALIDATION, BAD_GATEWAY),
)
async def upload_scan_to_cloud(
    scan_id: str,
    request: Request,
    provider_id: int,
    folder_id: str | None = Query(
        default=None, max_length=512,
        description="Destination folder: a folder id for Google Drive, a path (e.g. "
        "`/Receipts`) for Dropbox and OneDrive. Omit for the default `Papyrus Scans` folder.",
    ),
    user: User = Depends(require_permission("scan")),
    db: AsyncSession = Depends(get_db),
):
    """Upload a completed scan to one of the caller's connected cloud providers (Google Drive,
    Dropbox or OneDrive).

    Requires the `scan` permission. The response carries `file_id` (Drive, OneDrive) or `path`
    (Dropbox). `folder_id` is honoured by all three (id on Drive, path on Dropbox/OneDrive).
    """
    job = await _get_accessible_scan(db, request, user, scan_id)
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
            folder_id=folder_id or None,
        )
        return {"message": "Uploaded to Google Drive", "file_id": file_id}
    elif provider.provider == "dropbox":
        path = await cloud_service.upload_to_dropbox(
            filepath=job.filepath,
            filename=filename,
            access_token=access_token,
            **({"remote_path": _cloud_path(folder_id)} if folder_id else {}),
        )
        return {"message": "Uploaded to Dropbox", "path": path}
    else:
        file_id = await cloud_service.upload_to_onedrive(
            filepath=job.filepath,
            filename=filename,
            access_token=access_token,
            **({"folder_path": _cloud_path(folder_id)} if folder_id else {}),
        )
        return {"message": "Uploaded to OneDrive", "file_id": file_id}


@router.post(
    "/scans/{scan_id}/paperless",
    summary="Send a scan to Paperless-ngx",
    response_model=ScanPaperlessResponse,
    responses=std_responses(AUTH_ERRORS, NOT_FOUND, BAD_REQUEST, BAD_GATEWAY),
)
async def send_scan_to_paperless(
    scan_id: str,
    request: Request,
    user: User = Depends(require_permission("scan")),
    db: AsyncSession = Depends(get_db),
):
    """Push a completed scan to Paperless-ngx for archival.

    Requires the `scan` permission. 400 when Paperless-ngx is not configured.
    """
    job = await _get_accessible_scan(db, request, user, scan_id)
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


@router.post(
    "/scans/{scan_id}/ocr",
    summary="Run OCR on a scan",
    response_model=MessageResponse,
    responses=std_responses(AUTH_ERRORS, NOT_FOUND, BAD_REQUEST),
)
async def apply_ocr_to_scan(
    scan_id: str,
    request: Request,
    user: User = Depends(require_permission("scan")),
    db: AsyncSession = Depends(get_db),
):
    """Make a completed PDF scan searchable with OCR, rewriting the file in place.

    Requires the `scan` permission. PDF only (400 otherwise). Invalidates the cached thumbnail.
    """
    job = await _get_accessible_scan(db, request, user, scan_id)
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


@router.post(
    "/scans/{scan_id}/enhance",
    summary="Enhance a scan image",
    response_model=MessageResponse,
    responses=std_responses(AUTH_ERRORS, NOT_FOUND, BAD_REQUEST, VALIDATION),
)
async def enhance_scan(
    scan_id: str,
    request: Request,
    body: EnhanceRequest,
    user: User = Depends(require_permission("scan")),
    db: AsyncSession = Depends(get_db),
):
    """Apply brightness, contrast, rotation, crop and deskew to a completed image scan, rewriting
    the file in place.

    Requires the `scan` permission. Image scans only (png/jpeg/tiff); PDFs return 400. Invalidates
    the cached thumbnail.
    """
    job = await _get_accessible_scan(db, request, user, scan_id)
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


_BACKS_KEPT_MESSAGE = (
    "The pages could not be combined, so nothing was merged. "
    "The back-side scan was saved to your history."
)


@router.post(
    "/scans/{scan_id}/backs",
    summary="Scan the back sides of a feeder PDF",
    response_model=BacksResponse,
    responses=std_responses(
        AUTH_ERRORS, NOT_FOUND, BAD_REQUEST, VALIDATION, SOURCE_CONFLICT, SCANNER_BUSY
    ),
)
async def scan_backs(
    scan_id: str,
    request: Request,
    body: BacksRequest,
    user: User = Depends(require_permission("scan")),
    db: AsyncSession = Depends(get_db),
):
    """Scan the other side of a feeder PDF: the user flips the stack, the feeder scans the backs,
    and fronts and backs are interleaved into a NEW scan.

    Requires the `scan` permission; owner or admin only (others get 404). The fronts and the backs
    both stay in history. The fronts row is marked `duplex_merged` so neither merges again.
    Broadcasts `scan_completed` per row and fires audit/webhook/post-scan actions for the merged
    row. 400 when the scan is not an eligible completed feeder PDF; 409 when page counts mismatch
    (only the backs row is added) or the feeder is empty.
    """
    from app.routers.settings import get_setting

    job = await _get_accessible_scan(db, request, user, scan_id)
    if (
        job.status != "completed"
        or job.source != "ADF"
        or job.format != "pdf"
        or not job.filepath
        or not os.path.exists(job.filepath)
    ):
        raise BacksNotEligibleError(
            "Only a finished feeder scan saved as a PDF can have its other side scanned."
        )
    if job.duplex_merged:
        raise BacksNotEligibleError(
            "This scan is already part of a double-sided merge. "
            "Delete the merged scan first if you want to redo it."
        )
    if job.duplex_source_scan_id:
        raise BacksNotEligibleError(
            "This is the back side of a double-sided merge, so it has no other side to scan."
        )

    scanner = await db.get(Scanner, job.scanner_id) if job.scanner_id else None
    if scanner is None:
        scanner = await get_default_scanner(db)
    device = scanner.device if scanner else await get_default_scanner_device(db)
    scan_dir = await get_setting(db, "scan_dir") or "/app/data/scans"
    filename_template = (
        await get_setting(db, "scan_filename_template") or "scan_{date}_{time}_{id}"
    )
    front_scan_id, front_path = job.scan_id, job.filepath
    resolution, mode = job.resolution, job.mode
    scanner_id = job.scanner_id
    # Release the transaction before the long feeder scan, like /scan does.
    await db.commit()

    # Nothing has changed yet if this raises (busy scanner, empty feeder).
    backs_id, backs_path, backs_count = await scan_service.scan_batch(
        resolution=resolution, mode=mode, device=device, scan_dir=scan_dir,
    )

    async def live_scanner_id() -> int | None:
        """The scanner may have been deleted while the feeder was scanning."""
        if scanner_id is None:
            return None
        found = await db.execute(select(Scanner.id).where(Scanner.id == scanner_id))
        return found.scalar_one_or_none()

    async def new_row(row_scan_id: str, path: str, pages: int, merged: bool) -> ScanJob:
        return ScanJob(
            user_id=user.id,
            scan_id=row_scan_id,
            resolution=resolution,
            mode=mode,
            format="pdf",
            source="ADF",
            page_count=pages,
            filepath=path,
            file_size=os.path.getsize(path),
            status="completed",
            completed_at=datetime.now(timezone.utc),
            scanner_id=await live_scanner_id(),
            duplex_merged=merged,
            duplex_source_scan_id=front_scan_id,
        )

    async def snapshot(row: ScanJob) -> ScanResponse | None:
        """Fresh copy of a row, or None if it was deleted meanwhile."""
        try:
            await db.refresh(row)
        except Exception:
            return None
        return ScanResponse.model_validate(row, from_attributes=True)

    async def broadcast(*rows: ScanResponse | None) -> None:
        for row in rows:
            if row is not None:
                await ws_manager.broadcast("scans", {
                    "type": "scan_completed", "data": row.model_dump(mode="json")
                })

    async def keep_backs_only() -> None:
        kept = await new_row(backs_id, backs_path, backs_count, False)
        db.add(kept)
        await db.commit()
        await broadcast(await snapshot(kept))

    merged_id = str(uuid.uuid4())
    merged_path = os.path.join(scan_dir, f"{merged_id}.pdf")
    try:
        async with lock_for(front_path):
            outcome = await asyncio.to_thread(
                duplex_service.merge_backs_atomic,
                front_path,
                backs_path,
                merged_path,
                rotate_backs=body.rotate_backs,
                skip_blank_backs=body.skip_blank_backs,
                reverse_backs=body.reverse_backs,
            )
    except duplex_service.PageCountMismatchError as e:
        await keep_backs_only()
        raise BacksMismatchError(
            f"The front scan has {e.fronts} pages but the back scan has {e.backs}. "
            "Nothing was merged; both scans are in your history."
        )
    except Exception:
        logger.exception("Merging back sides failed")
        await keep_backs_only()
        raise BacksMismatchError(_BACKS_KEPT_MESSAGE)

    try:
        await db.refresh(job)
        if job.duplex_merged:
            raise RuntimeError("fronts were merged by a concurrent request")
        job.duplex_merged = True
        backs_row = await new_row(backs_id, backs_path, backs_count, False)
        merged_row = await new_row(merged_id, merged_path, outcome.page_count, True)
        db.add_all([backs_row, merged_row])
        await db.commit()
    except Exception:
        logger.exception("Recording the merged scan failed")
        await db.rollback()
        await asyncio.to_thread(cleanup_file, merged_path)
        await keep_backs_only()
        raise BacksMismatchError(_BACKS_KEPT_MESSAGE)

    fronts_resp = await snapshot(job)
    backs_resp = await snapshot(backs_row)
    merged_resp = await snapshot(merged_row)
    await broadcast(fronts_resp, backs_resp, merged_resp)

    # Everything below is best effort: the merge itself is already done.
    try:
        await log_event(db, "scan.complete", "scan_job", merged_id, user_id=user.id,
                        detail={"format": "pdf", "pages": outcome.page_count, "duplex": True})
        await db.commit()
        await dispatch_webhook(
            db, "scan.complete",
            {"scan_id": merged_id, "format": "pdf", "pages": outcome.page_count},
        )
        scanner = await db.get(Scanner, scanner_id) if scanner_id else None
        if scanner is None:
            scanner = await get_default_scanner(db)
        await db.commit()
        if scanner and scanner.auto_deliver:
            await run_post_scan_actions(
                merged_row, scanner, db, default_filename_template=filename_template
            )
            if os.path.exists(merged_path):
                merged_row.file_size = os.path.getsize(merged_path)
            await db.commit()
        refreshed = await snapshot(merged_row)
        if refreshed is not None:
            merged_resp = refreshed
            await broadcast(refreshed)
    except Exception:
        logger.exception("Post-merge delivery failed for scan %s", merged_id)
        await db.rollback()

    if merged_resp is None:
        raise NotFoundError("The merged scan was deleted before it could be returned.")
    return BacksResponse(
        merged=merged_resp, backs=backs_resp, fronts=fronts_resp,
        blanks_dropped=outcome.blanks_dropped,
    )


@router.post(
    "/scans/{scan_id}/smb",
    summary="Save a scan to an SMB share",
    response_model=MessageResponse,
    responses=std_responses(AUTH_ERRORS, NOT_FOUND, BAD_REQUEST, VALIDATION, BAD_GATEWAY),
)
async def save_scan_to_smb(
    scan_id: str,
    request: Request,
    share_id: int,
    remote_path: str = "/",
    user: User = Depends(require_permission("scan")),
    db: AsyncSession = Depends(get_db),
):
    """Write a completed scan to an SMB network share at `remote_path`.

    Requires the `scan` permission.
    """
    job = await _get_accessible_scan(db, request, user, scan_id)
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


@router.post(
    "/scans/bulk-delete",
    summary="Delete several scans",
    response_model=BulkDeleteResponse,
    responses=std_responses(AUTH_ERRORS, VALIDATION),
)
async def bulk_delete_scans(
    body: BulkDeleteScansRequest,
    request: Request,
    user: User = Depends(require_permission("scan")),
    db: AsyncSession = Depends(get_db),
):
    """Delete scans and their files by scan id; unknown and inaccessible ids are ignored.

    Requires the `scan` permission. Broadcasts `scan_deleted` per id (and `scan_completed` for
    fronts freed from a duplex merge).
    """
    result = await db.execute(select(ScanJob).where(ScanJob.scan_id.in_(body.scan_ids)))
    jobs = [j for j in result.scalars().all() if can_access_scan(user, request, j)]
    deleted_ids = [j.scan_id for j in jobs]

    deleted = 0
    filepaths: list[str] = []
    for job in jobs:
        if job.filepath:
            filepaths.append(job.filepath)
        await db.delete(job)
        deleted += 1
    freed = await duplex_service.release_fronts(db, jobs)

    # F44: rows are deleted and committed before their files are unlinked, so
    # a slow/failing unlink can never leave a committed row pointing at a
    # file that's already gone.
    await db.commit()

    for filepath in filepaths:
        await asyncio.to_thread(cleanup_file, filepath)

    for scan_id in deleted_ids:
        await ws_manager.broadcast("scans", {
            "type": "scan_deleted", "data": {"scan_id": scan_id}
        })
    for fronts in freed:
        await ws_manager.broadcast("scans", {
            "type": "scan_completed", "data": serialize_scan_job(fronts)
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


@router.post(
    "/scans/collate",
    summary="Merge scans into one PDF",
    response_model=ScanResponse,
    status_code=201,
    responses=std_responses(AUTH_ERRORS, NOT_FOUND, BAD_REQUEST, VALIDATION),
)
async def collate_scans(
    body: CollateRequest,
    request: Request,
    user: User = Depends(require_permission("scan")),
    db: AsyncSession = Depends(get_db),
):
    """Convert or merge completed scans into a single PDF, in the order given, stored as a new scan.

    Requires the `scan` permission. Originals are kept.
    """
    import uuid as _uuid

    result = await db.execute(select(ScanJob).where(ScanJob.scan_id.in_(body.scan_ids)))
    jobs_map = {
        j.scan_id: j for j in result.scalars().all() if can_access_scan(user, request, j)
    }

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


@router.delete(
    "/scans/{scan_id}",
    summary="Delete a scan",
    status_code=204,
    responses=std_responses(AUTH_ERRORS, NOT_FOUND),
)
async def delete_scan(
    scan_id: str,
    request: Request,
    user: User = Depends(require_permission("scan")),
    db: AsyncSession = Depends(get_db),
):
    """Delete a scan row and its file.

    Requires the `scan` permission. Fires the `scan.delete` webhook and broadcasts `scan_deleted` on
    the `scans` channel.
    """
    job = await _get_accessible_scan(db, request, user, scan_id)

    filepath = job.filepath
    scan_id_copy = job.scan_id
    await db.delete(job)
    freed = await duplex_service.release_fronts(db, [job])
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
    for fronts in freed:
        await ws_manager.broadcast("scans", {
            "type": "scan_completed", "data": serialize_scan_job(fronts)
        })


# --- Scan Profiles ---


@router.get(
    "/scan-profiles",
    summary="List scan profiles",
    response_model=list[ScanProfileResponse],
    responses=std_responses(AUTH_ERRORS),
)
async def list_profiles(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List the caller's own scan profiles, sorted by name.

    Any authenticated user.
    """
    result = await db.execute(
        select(ScanProfile).where(ScanProfile.user_id == user.id).order_by(ScanProfile.name)
    )
    return result.scalars().all()


@router.post(
    "/scan-profiles",
    summary="Create a scan profile",
    response_model=ScanProfileResponse,
    status_code=201,
    responses=std_responses(AUTH_ERRORS, VALIDATION),
)
async def create_profile(
    data: ScanProfileCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a scan profile owned by the caller.

    Any authenticated user.
    """
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


@router.put(
    "/scan-profiles/{profile_id}",
    summary="Update a scan profile",
    response_model=ScanProfileResponse,
    responses=std_responses(AUTH_ERRORS, NOT_FOUND, VALIDATION),
)
async def update_profile(
    profile_id: int,
    data: ScanProfileCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Replace all fields of one of the caller's scan profiles.

    Any authenticated user; other users' profiles return 404.
    """
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


@router.delete(
    "/scan-profiles/{profile_id}",
    summary="Delete a scan profile",
    status_code=204,
    responses=std_responses(AUTH_ERRORS, NOT_FOUND),
)
async def delete_profile(
    profile_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete one of the caller's scan profiles.

    Any authenticated user; other users' profiles return 404.
    """
    result = await db.execute(
        select(ScanProfile).where(ScanProfile.id == profile_id, ScanProfile.user_id == user.id)
    )
    profile = result.scalar_one_or_none()
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")

    await db.delete(profile)
    await db.commit()


# WebSocket endpoint for scan progress
@router.websocket("/ws/scans/{scan_id}")
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
    # The id may not exist yet (the client opens this before POSTing /scans); an existing scan
    # must be visible to this identity.
    async with async_session() as db:
        existing = (await db.execute(
            select(ScanJob).where(ScanJob.scan_id == scan_id)
        )).scalar_one_or_none()
    if existing is not None and not _scan_access(identity.user, identity.permissions, existing):
        await websocket.close(code=1008)
        return
    channel = f"scan:{scan_id}"
    await ws_manager.connect(channel, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(channel, websocket)
