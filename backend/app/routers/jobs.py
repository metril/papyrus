import asyncio
import logging
import os
import re
import secrets
import shutil
import sys
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import FileResponse

from app.auth.dependencies import get_current_user, require_permission
from app.database import get_db
from app.exceptions import ExternalServiceError, PrinterUnavailableError
from app.models import Printer, PrintJob, User
from app.schemas import (
    BulkDeleteJobsRequest,
    BulkDeleteResponse,
    PrintJobList,
    PrintJobResponse,
    serialize_print_job,
)
from app.services.audit_service import log_event
from app.services.convert_service import (
    CONVERTIBLE_MIMES,
    convert_to_pdf,
    is_printable,
    needs_conversion,
)
from app.services.cups_service import CupsService, get_default_release_queue_name
from app.services.file_service import (
    cleanup_file,
    detect_mime_type,
    get_upload_path,
    sanitize_filename,
    save_upload_streaming,
)
from app.services.throttle import Throttle
from app.services.thumbnail_service import THUMBNAIL_CACHE_CONTROL, get_or_create_thumbnail
from app.services.webhook_service import dispatch_webhook
from app.services.ws_manager import ws_manager

logger = logging.getLogger(__name__)

router = APIRouter()

# Mounted separately at /api (not /api/jobs) in main.py — the PWA share-target
# manifest action is a fixed, well-known path the browser navigates to
# directly, so it can't live under the jobs prefix.
share_target_router = APIRouter()

_PIN_RE = re.compile(r"^[0-9]{4,10}$")  # [0-9], not \d — \d is Unicode-aware

# Caps repeated release-PIN guesses on one held job at 5 failures / 5 minutes
# (F68) — a job's 4-digit PIN space (10,000 values) is otherwise sweepable by
# an authenticated printer user in well under an hour.
_release_pin_throttle = Throttle(max_failures=5, lockout_seconds=300)


async def _create_print_job_from_upload(
    db: AsyncSession,
    user: User,
    file: UploadFile,
    *,
    copies: int = 1,
    duplex: bool = False,
    media: str = "A4",
    hold: bool = True,
    release_pin: str = "",
    auto_pin: bool = True,
) -> tuple[PrintJob, str | None]:
    """Validate, stream-save, and create a print job from an uploaded file.

    This is the shared core behind ``POST /upload`` and ``POST
    /api/share-target`` so both pipelines stay byte-identical — validation,
    streaming save, PIN handling, held/auto-print dispatch, and the
    print.held webhook all live here exactly once.

    Raises HTTPException(400) for a missing filename or unsupported mime
    type. UploadTooLargeError (a PapyrusError, 413) propagates from
    save_upload_streaming to the global handler unchanged.

    Returns the created (and already committed/refreshed) job plus the
    plaintext PIN, if one was provided or generated — the caller decides
    whether/how to surface it.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    mime_type = detect_mime_type(file.filename)
    if not is_printable(mime_type):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {mime_type}. Accepted: PDF, images, office documents.",
        )

    # Validate a caller-supplied PIN before any file I/O: it's a String(10)
    # column, and previously an unvalidated PIN blew up at commit time as a
    # generic 500 *after* the file had already been streamed to disk,
    # orphaning it with no DB row (F115).
    pin = release_pin.strip() if release_pin else None
    if pin and not _PIN_RE.match(pin):
        raise HTTPException(status_code=400, detail="PIN must be 4–10 digits")

    from app.routers.settings import get_setting, safe_int_setting
    _upload_dir = await get_setting(db, "upload_dir") or "/app/data/uploads"
    _max_mb = safe_int_setting(await get_setting(db, "max_upload_size_mb"), 50)
    upload_path = get_upload_path(file.filename, upload_dir=_upload_dir)
    max_bytes = _max_mb * 1024 * 1024

    os.makedirs(os.path.dirname(upload_path), exist_ok=True)
    # UploadTooLargeError (a PapyrusError, 413) propagates to the global handler
    # and keeps its 413; any unexpected OSError falls through to the catch-all
    # handler as a generic 500 (no raw error text leaked to the client).
    file_size = await save_upload_streaming(file, upload_path, max_bytes)

    # Assign to default printer
    default_printer = await get_default_printer(db)

    # If no PIN was supplied, check whether the global setting requires one.
    # auto_pin=False skips this auto-generation for flows that have no way to
    # show the PIN to anyone (the share-target redirect) — an unseen PIN
    # would make the job permanently unreleasable.
    if not pin and auto_pin:
        require_pin_val = await get_setting(db, "require_release_pin") or ""
        require_pin = require_pin_val.lower() in ("true", "1", "yes")
        if require_pin:
            pin = f"{secrets.randbelow(10000):04d}"

    job = PrintJob(
        user_id=user.id,
        title=sanitize_filename(file.filename),
        filename=sanitize_filename(file.filename),
        filepath=upload_path,
        file_size=file_size,
        mime_type=mime_type,
        status="held" if hold else "converting" if needs_conversion(mime_type) else "printing",
        copies=copies,
        duplex=duplex,
        media=media,
        source_type="upload",
        printer_id=default_printer.id if default_printer else None,
        release_pin=pin,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    await ws_manager.broadcast("jobs", {
        "type": "job_created",
        "data": serialize_print_job(job),
    })

    # Every upload fires print.upload, regardless of hold/auto-print (F137) —
    # print.held below is the narrower "landed in the hold queue" signal.
    await dispatch_webhook(db, "print.upload", {
        "id": job.id,
        "title": job.title,
        "user_id": str(user.id),
        "source_type": job.source_type,
    })

    if hold:
        # The job landed in the hold queue — notify subscribers so they can
        # surface a "waiting for release" prompt. Auto-printed jobs (not hold)
        # never emit print.held.
        await dispatch_webhook(db, "print.held", {
            "id": job.id,
            "title": job.title,
            "user_id": str(user.id),
            "source_type": job.source_type,
        })
    else:
        await _process_job(job, db, default_printer)

    return job, pin


@router.post("/upload", response_model=PrintJobResponse, status_code=201)
async def upload_and_create_job(
    file: UploadFile = File(...),
    copies: int = Form(default=1),
    duplex: bool = Form(default=False),
    media: str = Form(default="A4"),
    hold: bool = Form(default=True),
    release_pin: str = Form(default=""),
    user: User = Depends(require_permission("print")),
    db: AsyncSession = Depends(get_db),
):
    """Upload a file and create a print job (held by default)."""
    job, pin = await _create_print_job_from_upload(
        db, user, file,
        copies=copies, duplex=duplex, media=media, hold=hold, release_pin=release_pin,
    )

    # Return the PIN in the initial response so the user can note it
    if pin and hold:
        resp = PrintJobResponse.model_validate(job, from_attributes=True)
        resp_dict = resp.model_dump(mode="json")
        resp_dict["release_pin"] = pin
        return JSONResponse(content=resp_dict, status_code=201)
    return job


async def _user_from_request_or_none(request: Request, db: AsyncSession) -> User | None:
    """Resolve the current user exactly like `get_current_user` (Bearer token
    or session cookie), but return None instead of raising HTTPException(401).

    Used only by the share-target route below: it's a browser navigation
    target (the OS share sheet POSTs here directly), so an unauthenticated
    request needs a 303 redirect to the login page, not a JSON 401 body.
    Calling `get_current_user` directly (rather than depending on it via
    `Depends`) and catching its 401 reuses the exact same auth resolution —
    Bearer token *and* session cookie — instead of duplicating the session
    user-lookup here and risking drift if that logic changes.
    """
    try:
        return await get_current_user(request, db)
    except HTTPException:
        return None


@share_target_router.post("/share-target")
async def share_target(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """PWA share-target action: the OS share sheet POSTs one or more files
    here (Android/Chromium only — iOS Safari ignores `share_target` in the
    manifest). Deliberately NOT the usual JSON-401 contract: this is a
    browser navigation, so unauthenticated requests redirect to the login
    page and authenticated ones redirect to the print queue, both 303.
    """
    user = await _user_from_request_or_none(request, db)
    if user is None:
        return RedirectResponse("/api/auth/login", status_code=303)
    # Scope-limited API tokens need the "print" permission here, matching
    # /upload. Session users pass (token_permissions is None = full access).
    token_permissions = getattr(request.state, "token_permissions", None)
    if token_permissions is not None and "print" not in token_permissions:
        return RedirectResponse("/api/auth/login", status_code=303)

    form = await request.form()
    failed = 0
    for shared_file in form.getlist("file"):
        if isinstance(shared_file, str):
            continue  # not a file part; ignore a stray non-file "file" field
        # auto_pin=False: this flow redirects immediately and can never show a
        # generated PIN, which would leave the job unreleasable under
        # require_release_pin. Shared jobs are held without a PIN instead.
        try:
            await _create_print_job_from_upload(db, user, shared_file, auto_pin=False)
        except Exception as e:
            # One bad file (unsupported type, oversize, ...) must not abort
            # the rest of the share — accumulate failures and keep going
            # (F133), or the browser lands on a raw JSON error page instead
            # of the print queue, and every later file is silently dropped.
            logger.warning("share-target: failed to add %r: %s", shared_file.filename, e)
            failed += 1

    redirect_url = "/print" if not failed else f"/print?share_failed={failed}"
    return RedirectResponse(redirect_url, status_code=303)


async def get_default_printer(db: AsyncSession):
    result = await db.execute(
        select(Printer).where(Printer.is_default.is_(True), Printer.is_network_queue.is_(False))
    )
    return result.scalar_one_or_none()


async def _process_job(job: PrintJob, db: AsyncSession, printer=None):
    """Convert (if needed) and send job to the designated CUPS release queue."""
    print_path = job.filepath

    try:
        # Resolve which CUPS queue to print to. Done inside the try (not
        # before it) so a missing default printer — or any other failure
        # here — lands the job in "failed" with a curated message and a
        # broadcast, the same as a downstream CUPS failure, rather than
        # propagating as an unhandled 503 with the job stuck mid-state.
        release_queue = None
        if printer is None and job.printer_id:
            printer = await db.get(Printer, job.printer_id)
        if printer and not printer.is_network_queue:
            release_queue = f"{printer.cups_name}_release"
        else:
            # F9: the hold-queue name (get_default_printer_name) re-enters the
            # CUPS backend script and ingest pipeline instead of printing.
            release_queue = await get_default_release_queue_name(db)

        svc = CupsService(printer_name=release_queue)

        if needs_conversion(job.mime_type):
            job.status = "converting"
            await db.commit()
            # The UPDATE flush expires the server-generated updated_at; reload
            # before the synchronous serialization or it lazy-loads and crashes.
            await db.refresh(job)
            await ws_manager.broadcast("jobs", {
                "type": "job_updated", "data": serialize_print_job(job)
            })
            output_dir = os.path.dirname(job.filepath)
            print_path = await convert_to_pdf(job.filepath, output_dir)

        job.status = "printing"
        await db.commit()
        await db.refresh(job)
        await ws_manager.broadcast("jobs", {
            "type": "job_updated", "data": serialize_print_job(job)
        })

        cups_job_id = await svc.create_held_job(
            filepath=print_path,
            title=job.title,
            copies=job.copies,
            duplex=job.duplex,
            media=job.media,
        )
        job.cups_job_id = cups_job_id
        await svc.release_job(cups_job_id)

        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(job)
        await ws_manager.broadcast("jobs", {
            "type": "job_updated", "data": serialize_print_job(job)
        })

    except Exception:
        logger.exception("Print job %s failed during processing", job.id)
        job.status = "failed"
        job.error_message = "Printing failed — check the printer connection and file format."
        await db.commit()
        await db.refresh(job)
        await ws_manager.broadcast("jobs", {
            "type": "job_updated", "data": serialize_print_job(job)
        })
    finally:
        # F31: convert_to_pdf's temp dir is only ours to clean up once CUPS
        # has copied the file into its own spool (or we're giving up on it) —
        # print_path only differs from job.filepath when conversion produced
        # one.
        if print_path != job.filepath:
            await asyncio.to_thread(shutil.rmtree, os.path.dirname(print_path), ignore_errors=True)


@router.post("/internal/ingest", response_model=PrintJobResponse, status_code=201)
async def ingest_network_job(
    request: Request,
    file: UploadFile = File(...),
    title: str = Form(default="Untitled"),
    username: str = Form(default="unknown"),
    copies: int = Form(default=1),
    duplex: bool = Form(default=False),
    media: str = Form(default="A4"),
    queue_name: str = Form(default=""),
    db: AsyncSession = Depends(get_db),
):
    """Internal endpoint for the CUPS backend to submit network print jobs.

    Only accessible from localhost (called by the papyrus CUPS backend script).
    """
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1"):
        raise HTTPException(status_code=403, detail="Internal endpoint only")

    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    from app.routers.settings import get_setting
    _upload_dir = await get_setting(db, "upload_dir") or "/app/data/uploads"
    upload_path = get_upload_path(file.filename, upload_dir=_upload_dir)
    # No configured size cap for network jobs (internal, localhost-only endpoint);
    # stream unbounded to preserve prior behavior while avoiding full in-RAM buffering.
    file_size = await save_upload_streaming(file, upload_path, max_bytes=sys.maxsize)
    if not file_size:
        cleanup_file(upload_path)
        raise HTTPException(status_code=400, detail="Empty file")

    # Resolve printer from queue_name
    printer = None
    if queue_name:
        result = await db.execute(select(Printer).where(Printer.cups_name == queue_name))
        printer = result.scalar_one_or_none()
    if printer is None:
        printer = await get_default_printer(db)

    auto_release = printer.auto_release if printer else False

    job = PrintJob(
        title=sanitize_filename(title),
        filename=sanitize_filename(file.filename),
        filepath=upload_path,
        file_size=file_size,
        mime_type=detect_mime_type(file.filename),
        status="held",
        copies=copies,
        duplex=duplex,
        media=media,
        source_type="network",
        printer_id=printer.id if printer else None,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    await ws_manager.broadcast("jobs", {
        "type": "job_created",
        "data": serialize_print_job(job),
    })

    if auto_release:
        await _process_job(job, db, printer)
    else:
        # Held network job (auto-release off) — notify subscribers. When
        # auto_release is on the job is printed immediately, so no print.held.
        await dispatch_webhook(db, "print.held", {
            "id": job.id,
            "title": job.title,
            "username": username,
            "source_type": job.source_type,
        })

    return job


@router.get("", response_model=PrintJobList)
async def list_jobs(
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    user: User = Depends(require_permission("print")),
    db: AsyncSession = Depends(get_db),
):
    """List print jobs, optionally filtered by status."""
    query = select(PrintJob).order_by(PrintJob.created_at.desc())
    count_query = select(func.count(PrintJob.id))

    if status:
        query = query.where(PrintJob.status == status)
        count_query = count_query.where(PrintJob.status == status)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    result = await db.execute(query.limit(limit).offset(offset))
    jobs = result.scalars().all()

    return PrintJobList(jobs=jobs, total=total)


@router.get("/{job_id}", response_model=PrintJobResponse)
async def get_job(
    job_id: int,
    user: User = Depends(require_permission("print")),
    db: AsyncSession = Depends(get_db),
):
    """Get a single print job."""
    result = await db.execute(select(PrintJob).where(PrintJob.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _pin_grants_access(provided_pin: str | None, stored_pin: str) -> bool:
    """Constant-time compare of a query-param `pin` against a job's
    release_pin — the same byte-wise `secrets.compare_digest` pattern
    `release_job` uses, guarding the same non-ASCII edge case so a malformed
    pin param 403s instead of 500ing."""
    if not provided_pin:
        return False
    try:
        return secrets.compare_digest(provided_pin.encode(), stored_pin.encode())
    except (UnicodeEncodeError, AttributeError, TypeError):
        return False


def _require_job_owner_or_admin(job: PrintJob, user: User) -> None:
    """Guard destructive actions (cancel/delete) on another user's job (F27).

    A job with no owner (`user_id` NULL — network jobs) is fair game for any
    print user, matching the existing shared-queue policy for those. A job
    someone else owns requires the requester to be an admin.
    """
    if job.user_id is not None and job.user_id != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="Not authorized to modify this job")


def _require_file_access(job: PrintJob, user: User, pin: str | None) -> None:
    """Guard access to a print job's underlying file — download, preview, and
    thumbnail all share this (F27). Without it, `release_pin` protected only
    the paper output: any authenticated print user could fetch another
    user's confidential PDF by id with no PIN check at all.

    Allowed: the job's owner, an admin, or anyone supplying the correct
    release PIN via the `pin` query param. Jobs without a PIN are unaffected
    — the shared print queue's file access stays open to any print user.

    Shares `release_job`'s per-job PIN throttle (F68, same `pin:{job_id}` key)
    — without that, this query-param check would be a second, unthrottled
    oracle over the same 10,000-value PIN space release_job already
    rate-limits, defeating the lockout entirely.
    """
    if not job.release_pin:
        return
    if job.user_id is not None and job.user_id == user.id:
        return
    if user.role == "admin":
        return
    throttle_key = f"pin:{job.id}"
    _release_pin_throttle.check(throttle_key)
    if _pin_grants_access(pin, job.release_pin):
        _release_pin_throttle.reset(throttle_key)
        return
    _release_pin_throttle.record_failure(throttle_key)
    raise HTTPException(status_code=403, detail="PIN required to access this file")


@router.get("/{job_id}/download")
async def download_job_file(
    job_id: int,
    pin: str | None = None,
    user: User = Depends(require_permission("print")),
    db: AsyncSession = Depends(get_db),
):
    """Download a print job's file."""
    result = await db.execute(select(PrintJob).where(PrintJob.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    _require_file_access(job, user, pin)
    if not job.filepath or not os.path.exists(job.filepath):
        raise HTTPException(status_code=404, detail="File not found on disk")
    return FileResponse(
        job.filepath,
        filename=job.filename,
        media_type=job.mime_type,
        content_disposition_type="inline",
    )


async def _ensure_preview_pdf(job: PrintJob) -> str:
    """Return a PDF path to use as the preview/thumbnail source for `job`.

    PDF and image jobs are returned unchanged (`job.filepath`). Office docs
    are converted to PDF via LibreOffice and cached alongside the original as
    `<filepath>.preview.pdf` so repeat callers (preview endpoint, thumbnail
    endpoint) reuse the same converted file instead of reconverting.

    Raises:
        ExternalServiceError: if the LibreOffice conversion fails.
    """
    if job.mime_type not in CONVERTIBLE_MIMES:
        return job.filepath

    preview_path = job.filepath + ".preview.pdf"
    if not os.path.exists(preview_path):
        output_dir = os.path.dirname(job.filepath)
        converted = None
        try:
            converted = await convert_to_pdf(job.filepath, output_dir)
            os.replace(converted, preview_path)
        except RuntimeError as e:
            raise ExternalServiceError(
                "Converting the document for preview failed."
            ) from e
        finally:
            # convert_to_pdf wrote `converted` into its own unique temp dir
            # (F30); clean it up in `finally` — whether the replace succeeded
            # (now-empty dir) or raised (dir still holds the PDF) — so a
            # failed replace can't leak it. `converted` stays None if
            # convert_to_pdf itself raised (it cleans up its own temp dir on
            # failure), so there's nothing to remove in that case.
            if converted is not None:
                await asyncio.to_thread(
                    shutil.rmtree, os.path.dirname(converted), ignore_errors=True
                )
    return preview_path


@router.get("/{job_id}/preview")
async def preview_job_file(
    job_id: int,
    pin: str | None = None,
    user: User = Depends(require_permission("print")),
    db: AsyncSession = Depends(get_db),
):
    """Preview a print job's file. Converts office docs to PDF on the fly."""
    result = await db.execute(select(PrintJob).where(PrintJob.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    _require_file_access(job, user, pin)
    if not job.filepath or not os.path.exists(job.filepath):
        raise HTTPException(status_code=404, detail="File not found on disk")

    # PDF and images can be served directly
    if job.mime_type not in CONVERTIBLE_MIMES:
        return FileResponse(
            job.filepath,
            filename=job.filename,
            media_type=job.mime_type,
            content_disposition_type="inline",
        )

    # Office docs: convert to PDF, cache as .preview.pdf
    preview_path = await _ensure_preview_pdf(job)

    pdf_name = os.path.splitext(job.filename)[0] + ".pdf"
    return FileResponse(
        preview_path,
        filename=pdf_name,
        media_type="application/pdf",
        content_disposition_type="inline",
    )


@router.get("/{job_id}/thumbnail")
async def get_job_thumbnail(
    job_id: int,
    pin: str | None = None,
    user: User = Depends(require_permission("print")),
    db: AsyncSession = Depends(get_db),
):
    """Return a small cached preview (~320px) of a held job's file.

    PDF and image jobs are thumbnailed directly from `job.filepath`. Office
    docs are converted to PDF first (via `_ensure_preview_pdf`, sharing the
    same `.preview.pdf` cache as `/preview`) before being thumbnailed.
    Generated on first request and reused after.
    """
    result = await db.execute(select(PrintJob).where(PrintJob.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    _require_file_access(job, user, pin)
    if not job.filepath or not os.path.exists(job.filepath):
        raise HTTPException(status_code=404, detail="File not found on disk")

    source = await _ensure_preview_pdf(job)

    try:
        thumb_path = await get_or_create_thumbnail(source)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found on disk")

    return FileResponse(
        thumb_path,
        media_type="image/jpeg",
        content_disposition_type="inline",
        headers={"Cache-Control": THUMBNAIL_CACHE_CONTROL},
    )


class PrinterAssign(BaseModel):
    printer_id: int


@router.patch("/{job_id}/printer", response_model=PrintJobResponse)
async def assign_printer(
    job_id: int,
    body: PrinterAssign,
    user: User = Depends(require_permission("print")),
    db: AsyncSession = Depends(get_db),
):
    """Reassign a held job to a different printer."""
    result = await db.execute(select(PrintJob).where(PrintJob.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "held":
        raise HTTPException(status_code=400, detail="Can only reassign held jobs")

    printer = await db.get(Printer, body.printer_id)
    if printer is None:
        raise HTTPException(status_code=404, detail="Printer not found")
    if printer.is_network_queue:
        raise HTTPException(status_code=400, detail="Cannot assign job to network queue")

    job.printer_id = printer.id
    await db.commit()
    await db.refresh(job)
    await ws_manager.broadcast("jobs", {
        "type": "job_updated",
        "data": serialize_print_job(job),
    })
    return job


class ReleaseRequest(BaseModel):
    pin: str | None = None


@router.post("/{job_id}/release", response_model=PrintJobResponse)
async def release_job(
    job_id: int,
    body: ReleaseRequest | None = None,
    user: User = Depends(require_permission("print")),
    db: AsyncSession = Depends(get_db),
):
    """Release a held job to start printing. Requires PIN if one was set."""
    result = await db.execute(select(PrintJob).where(PrintJob.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "held":
        raise HTTPException(status_code=400, detail=f"Job is not held (status: {job.status})")

    # Validate release PIN if one is set on the job. Throttled (F68) — the
    # PIN space is only 10,000 4-digit values, easily swept by an
    # authenticated printer user without a per-job attempt cap — and compared
    # with the same constant-time helper the file-access endpoints use, so a
    # timing side channel can't narrow the guess.
    if job.release_pin:
        throttle_key = f"pin:{job_id}"
        _release_pin_throttle.check(throttle_key)
        provided_pin = body.pin if body else None
        if not _pin_grants_access(provided_pin, job.release_pin):
            _release_pin_throttle.record_failure(throttle_key)
            raise HTTPException(status_code=403, detail="Invalid or missing release PIN")
        _release_pin_throttle.reset(throttle_key)

    printer = None
    if job.printer_id:
        printer = await db.get(Printer, job.printer_id)

    # Capture scalars before commits expire objects
    job_id = job.id
    job_title = job.title
    job_copies = job.copies
    user_id = user.id
    print_path = job.filepath

    # F28: lock the row for the actual release, immediately before any CUPS
    # work. A second concurrent release blocks here until the first commits,
    # then observes status != "held" and gets 409 instead of both requests
    # passing the plain status check above and printing the job twice.
    #
    # populate_existing=True is required: `job` is already identity-mapped
    # into this session from the plain select above, and with
    # expire_on_commit=False (app/database.py) nothing expires it, so
    # SQLAlchemy would otherwise return that same cached instance — with its
    # stale (pre-lock) column values — instead of what the FOR UPDATE
    # re-select actually just read off the row.
    locked_result = await db.execute(
        select(PrintJob)
        .where(PrintJob.id == job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    job = locked_result.scalar_one_or_none()
    if job is None or job.status != "held":
        raise HTTPException(status_code=409, detail="Job is already being released")

    try:
        if needs_conversion(job.mime_type):
            job.status = "converting"
            await db.commit()
            # The UPDATE flush expires the server-generated updated_at; reload
            # before the synchronous serialization or it lazy-loads and crashes.
            await db.refresh(job)
            await ws_manager.broadcast("jobs", {
                "type": "job_updated", "data": serialize_print_job(job)
            })
            output_dir = os.path.dirname(job.filepath)
            print_path = await convert_to_pdf(job.filepath, output_dir)

        # Use the printer's release queue or fall back to default
        if printer and not printer.is_network_queue:
            queue = f"{printer.cups_name}_release"
        else:
            # F9: the hold-queue name (get_default_printer_name) re-enters the
            # CUPS backend script and ingest pipeline instead of printing.
            queue = await get_default_release_queue_name(db)

        svc = CupsService(printer_name=queue)
        cups_job_id = await svc.create_held_job(
            filepath=print_path,
            title=job_title,
            copies=job_copies,
            duplex=job.duplex,
            media=job.media,
        )
        job.cups_job_id = cups_job_id
        await svc.release_job(cups_job_id)

        job.status = "printing"
        await db.commit()
        await db.refresh(job)
        await log_event(db, "print.release", "print_job", str(job_id),
                        user_id=user_id, detail={"title": job_title, "copies": job_copies})
        await db.commit()
        await dispatch_webhook(
            db, "print.release", {"id": job_id, "title": job_title, "copies": job_copies}
        )
        await ws_manager.broadcast("jobs", {
            "type": "job_updated", "data": serialize_print_job(job)
        })

    except Exception as e:
        logger.exception("Releasing print job %s failed", job_id)
        try:
            await db.rollback()
            job.status = "failed"
            job.error_message = "Printing failed — check the printer connection and file format."
            await db.commit()
            # rollback expired the ORM object; reload before serializing so the
            # broadcast carries a fully-populated job.
            await db.refresh(job)
            await ws_manager.broadcast("jobs", {
                "type": "job_updated", "data": serialize_print_job(job)
            })
        except Exception:
            pass
        # The rollback/mark-failed/broadcast side effects above are preserved;
        # the client gets a curated 502 instead of the raw exception text.
        raise ExternalServiceError(
            "Releasing the job failed. Check the printer connection."
        ) from e
    finally:
        # F31: convert_to_pdf's temp dir is only ours to clean up once CUPS
        # has copied the file into its own spool (or we're giving up on it) —
        # print_path only differs from job.filepath when conversion produced
        # one.
        if print_path != job.filepath:
            await asyncio.to_thread(shutil.rmtree, os.path.dirname(print_path), ignore_errors=True)

    return job


@router.post("/{job_id}/cancel", response_model=PrintJobResponse)
async def cancel_job(
    job_id: int,
    user: User = Depends(require_permission("print")),
    db: AsyncSession = Depends(get_db),
):
    """Cancel a print job."""
    result = await db.execute(select(PrintJob).where(PrintJob.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    _require_job_owner_or_admin(job, user)

    if job.cups_job_id:
        # Try to cancel via CUPS — use the job's printer or default
        try:
            printer = await db.get(Printer, job.printer_id) if job.printer_id else None
            queue = (
                f"{printer.cups_name}_release"
                if printer and not printer.is_network_queue
                # F9: the hold-queue name re-enters the CUPS backend script
                # and ingest pipeline instead of targeting the release queue.
                else await get_default_release_queue_name(db)
            )
            await CupsService(printer_name=queue).cancel_job(job.cups_job_id)
        except Exception:
            pass

    job.status = "cancelled"
    # Capture scalars before commit expires objects
    job_id = job.id
    job_title = job.title
    user_id = user.id
    await db.commit()
    await db.refresh(job)

    try:
        await ws_manager.broadcast("jobs", {
            "type": "job_updated", "data": serialize_print_job(job)
        })
        await log_event(db, "print.cancel", "print_job", str(job_id),
                        user_id=user_id, detail={"title": job_title})
        await db.commit()
    except Exception:
        await db.rollback()
    return job


@router.post("/bulk-delete", response_model=BulkDeleteResponse)
async def bulk_delete_jobs(
    body: BulkDeleteJobsRequest,
    user: User = Depends(require_permission("print")),
    db: AsyncSession = Depends(get_db),
):
    """Delete multiple print jobs and their files."""
    result = await db.execute(select(PrintJob).where(PrintJob.id.in_(body.ids)))
    jobs = result.scalars().all()

    # Batch-load all referenced printers and resolve the default printer name
    # once, instead of a `db.get(Printer, ...)` + default-printer query per job.
    printer_ids = {job.printer_id for job in jobs if job.printer_id}
    printers_by_id: dict[int, Printer] = {}
    if printer_ids:
        printers_result = await db.execute(select(Printer).where(Printer.id.in_(printer_ids)))
        printers_by_id = {p.id: p for p in printers_result.scalars().all()}

    default_printer_name = None
    if any(job.cups_job_id and job.status in ("held", "printing") for job in jobs):
        try:
            # F9: the hold-queue name re-enters the CUPS backend script and
            # ingest pipeline instead of targeting the release queue. This
            # cancel is best-effort (matching the per-job try/except below),
            # so a missing default printer must not block deleting the rows.
            default_printer_name = await get_default_release_queue_name(db)
        except PrinterUnavailableError:
            default_printer_name = None

    deleted_ids: list[int] = []
    for job in jobs:
        # F27: another user's job requires admin; a NULL-owner (network) job
        # is fair game for any print user. Silently skip rather than fail
        # the whole batch over one unauthorized id.
        if job.user_id is not None and job.user_id != user.id and user.role != "admin":
            continue
        if job.cups_job_id and job.status in ("held", "printing"):
            try:
                printer = printers_by_id.get(job.printer_id) if job.printer_id else None
                queue = (
                    f"{printer.cups_name}_release"
                    if printer and not printer.is_network_queue
                    else default_printer_name
                )
                await CupsService(printer_name=queue).cancel_job(job.cups_job_id)
            except Exception:
                pass
        cleanup_file(job.filepath)
        await db.delete(job)
        deleted_ids.append(job.id)

    await db.commit()

    for job_id in deleted_ids:
        await ws_manager.broadcast("jobs", {
            "type": "job_deleted", "data": {"id": job_id}
        })

    return BulkDeleteResponse(deleted=len(deleted_ids))


@router.delete("/{job_id}", status_code=204)
async def delete_job(
    job_id: int,
    user: User = Depends(require_permission("print")),
    db: AsyncSession = Depends(get_db),
):
    """Delete a job record and its file."""
    result = await db.execute(select(PrintJob).where(PrintJob.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    _require_job_owner_or_admin(job, user)

    if job.cups_job_id and job.status in ("held", "printing"):
        try:
            printer = await db.get(Printer, job.printer_id) if job.printer_id else None
            queue = (
                f"{printer.cups_name}_release"
                if printer and not printer.is_network_queue
                # F9: the hold-queue name re-enters the CUPS backend script
                # and ingest pipeline instead of targeting the release queue.
                else await get_default_release_queue_name(db)
            )
            await CupsService(printer_name=queue).cancel_job(job.cups_job_id)
        except Exception:
            pass

    cleanup_file(job.filepath)
    job_id_copy = job.id
    title_copy = job.title
    user_id = user.id
    await db.delete(job)
    await db.commit()

    try:
        await log_event(db, "print.delete", "print_job", str(job_id_copy),
                        user_id=user_id, detail={"title": title_copy})
        await db.commit()
        await dispatch_webhook(db, "print.delete", {"id": job_id_copy, "title": title_copy})
        await ws_manager.broadcast("jobs", {
            "type": "job_deleted", "data": {"id": job_id_copy}
        })
    except Exception:
        await db.rollback()


class ReprintRequest(BaseModel):
    pin: str | None = None


@router.post("/{job_id}/reprint", response_model=PrintJobResponse, status_code=201)
async def reprint_job(
    job_id: int,
    body: ReprintRequest | None = None,
    user: User = Depends(require_permission("print")),
    db: AsyncSession = Depends(get_db),
):
    """Re-create a print job from an existing completed/failed/cancelled job."""
    result = await db.execute(select(PrintJob).where(PrintJob.id == job_id))
    original = result.scalar_one_or_none()
    if original is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if not original.filepath or not os.path.exists(original.filepath):
        raise HTTPException(status_code=400, detail="Original file no longer available")

    # F27: reprinting hands the requester a brand-new row *they* own — for a
    # PIN-protected original, that would otherwise be an unprotected copy of
    # someone else's confidential file. Gate it like file access (owner/admin
    # bypass; anyone else needs the correct PIN) and share release_job's
    # throttle (same pin:{job_id} key) so this isn't a second, unthrottled
    # oracle over the PIN space.
    if original.release_pin:
        is_owner = original.user_id is not None and original.user_id == user.id
        if not is_owner and user.role != "admin":
            throttle_key = f"pin:{job_id}"
            _release_pin_throttle.check(throttle_key)
            provided_pin = body.pin if body else None
            if not _pin_grants_access(provided_pin, original.release_pin):
                _release_pin_throttle.record_failure(throttle_key)
                raise HTTPException(status_code=403, detail="PIN required to reprint this file")
            _release_pin_throttle.reset(throttle_key)

    default_printer = await get_default_printer(db)

    # Verify original printer still exists; fall back to default
    reprint_printer_id = None
    if original.printer_id:
        existing_printer = await db.get(Printer, original.printer_id)
        if existing_printer:
            reprint_printer_id = existing_printer.id
    if not reprint_printer_id and default_printer:
        reprint_printer_id = default_printer.id

    # F29: copy the file to a fresh upload path instead of aliasing
    # original.filepath — deleting either job's row (or retention doing it
    # unattended) must not unlink the file out from under the other one.
    from app.routers.settings import get_setting
    upload_dir = await get_setting(db, "upload_dir") or "/app/data/uploads"
    new_path = get_upload_path(original.filename, upload_dir=upload_dir)
    os.makedirs(os.path.dirname(new_path), exist_ok=True)
    await asyncio.to_thread(shutil.copy2, original.filepath, new_path)

    new_job = PrintJob(
        user_id=user.id,
        title=original.title,
        filename=original.filename,
        filepath=new_path,
        file_size=original.file_size,
        mime_type=original.mime_type,
        status="held",
        copies=original.copies,
        duplex=original.duplex,
        media=original.media,
        source_type="upload",
        printer_id=reprint_printer_id,
        # F27: carry the PIN forward so the reprinted copy stays protected —
        # it must not be an unprotected duplicate of a confidential original.
        release_pin=original.release_pin,
    )
    db.add(new_job)
    await db.commit()
    await db.refresh(new_job)

    await ws_manager.broadcast("jobs", {
        "type": "job_created",
        "data": serialize_print_job(new_job),
    })

    return new_job
