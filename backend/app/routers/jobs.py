import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.responses import FileResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_permission
from app.database import get_db
from app.services.audit_service import log_event
from app.services.webhook_service import dispatch_webhook
from app.models import Printer, PrintJob, User
from app.schemas import BulkDeleteJobsRequest, BulkDeleteResponse, PrintJobList, PrintJobResponse
from app.services.convert_service import convert_to_pdf, is_printable, needs_conversion
from app.services.cups_service import CupsService, get_default_printer_name
from app.services.file_service import (
    cleanup_file,
    detect_mime_type,
    get_upload_path,
    sanitize_filename,
    validate_upload_size,
)
from app.services.ws_manager import ws_manager

router = APIRouter()


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
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    mime_type = detect_mime_type(file.filename)
    if not is_printable(mime_type):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {mime_type}. Accepted: PDF, images, office documents.",
        )

    from app.routers.settings import get_setting
    _upload_dir = await get_setting(db, "upload_dir") or "/app/data/uploads"
    _max_mb = int(await get_setting(db, "max_upload_size_mb") or 50)
    upload_path = get_upload_path(file.filename, upload_dir=_upload_dir)
    content = await file.read()

    if not validate_upload_size(len(content), max_upload_size_mb=_max_mb):
        raise HTTPException(status_code=413, detail="File too large")

    os.makedirs(os.path.dirname(upload_path), exist_ok=True)
    try:
        with open(upload_path, "wb") as f:
            f.write(content)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {e}")

    # Assign to default printer
    default_printer = await get_default_printer(db)

    # Determine PIN: use provided value, or check if global setting requires one
    pin = release_pin.strip() if release_pin else None
    if not pin:
        from app.routers.settings import get_setting
        require_pin_val = await get_setting(db, "require_release_pin") or ""
        require_pin = require_pin_val.lower() in ("true", "1", "yes")
        if require_pin:
            import secrets
            pin = f"{secrets.randbelow(10000):04d}"

    job = PrintJob(
        user_id=user.id,
        title=sanitize_filename(file.filename),
        filename=sanitize_filename(file.filename),
        filepath=upload_path,
        file_size=len(content),
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
        "data": {"id": job.id, "title": job.title, "status": job.status, "source_type": "upload"},
    })

    if not hold:
        await _process_job(job, db, default_printer)

    # Return the PIN in the initial response so the user can note it
    if pin and hold:
        resp = PrintJobResponse.model_validate(job, from_attributes=True)
        resp_dict = resp.model_dump(mode="json")
        resp_dict["release_pin"] = pin
        return JSONResponse(content=resp_dict, status_code=201)
    return job


async def get_default_printer(db: AsyncSession):
    result = await db.execute(
        select(Printer).where(Printer.is_default == True, Printer.is_network_queue == False)
    )
    return result.scalar_one_or_none()


async def _process_job(job: PrintJob, db: AsyncSession, printer=None):
    """Convert (if needed) and send job to the designated CUPS release queue."""
    print_path = job.filepath

    # Resolve which CUPS queue to print to
    release_queue = None
    if printer is None and job.printer_id:
        printer = await db.get(Printer, job.printer_id)
    if printer and not printer.is_network_queue:
        release_queue = f"{printer.cups_name}_release"
    else:
        release_queue = await get_default_printer_name(db)

    svc = CupsService(printer_name=release_queue)

    try:
        if needs_conversion(job.mime_type):
            job.status = "converting"
            await db.commit()
            await ws_manager.broadcast("jobs", {
                "type": "job_updated", "data": {"id": job.id, "status": "converting"}
            })
            output_dir = os.path.dirname(job.filepath)
            print_path = await convert_to_pdf(job.filepath, output_dir)

        job.status = "printing"
        await db.commit()
        await ws_manager.broadcast("jobs", {
            "type": "job_updated", "data": {"id": job.id, "status": "printing"}
        })

        cups_job_id = svc.create_held_job(
            filepath=print_path,
            title=job.title,
            copies=job.copies,
            duplex=job.duplex,
            media=job.media,
        )
        job.cups_job_id = cups_job_id
        svc.release_job(cups_job_id)

        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc)
        await db.commit()
        await ws_manager.broadcast("jobs", {
            "type": "job_updated", "data": {"id": job.id, "status": "completed"}
        })

    except Exception as e:
        job.status = "failed"
        job.error_message = str(e)
        await db.commit()
        await ws_manager.broadcast("jobs", {
            "type": "job_updated",
            "data": {"id": job.id, "status": "failed", "error": str(e)},
        })


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

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")

    from app.routers.settings import get_setting
    _upload_dir = await get_setting(db, "upload_dir") or "/app/data/uploads"
    upload_path = get_upload_path(file.filename, upload_dir=_upload_dir)
    with open(upload_path, "wb") as f:
        f.write(content)

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
        file_size=len(content),
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
        "data": {"id": job.id, "title": job.title, "status": "held", "source_type": "network"},
    })

    if auto_release:
        await _process_job(job, db, printer)

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


@router.get("/{job_id}/download")
async def download_job_file(
    job_id: int,
    user: User = Depends(require_permission("print")),
    db: AsyncSession = Depends(get_db),
):
    """Download a print job's file."""
    result = await db.execute(select(PrintJob).where(PrintJob.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.filepath or not os.path.exists(job.filepath):
        raise HTTPException(status_code=404, detail="File not found on disk")
    return FileResponse(
        job.filepath,
        filename=job.filename,
        media_type=job.mime_type,
        content_disposition_type="inline",
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
        "data": {"id": job.id, "status": job.status, "printer_id": job.printer_id},
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

    # Validate release PIN if one is set on the job
    if job.release_pin:
        provided_pin = body.pin if body else None
        if not provided_pin or provided_pin != job.release_pin:
            raise HTTPException(status_code=403, detail="Invalid or missing release PIN")

    printer = None
    if job.printer_id:
        printer = await db.get(Printer, job.printer_id)

    try:
        print_path = job.filepath
        if needs_conversion(job.mime_type):
            job.status = "converting"
            await db.commit()
            await ws_manager.broadcast("jobs", {
                "type": "job_updated", "data": {"id": job.id, "status": "converting"}
            })
            output_dir = os.path.dirname(job.filepath)
            print_path = await convert_to_pdf(job.filepath, output_dir)

        # Use the printer's release queue or fall back to default
        if printer and not printer.is_network_queue:
            queue = f"{printer.cups_name}_release"
        else:
            queue = await get_default_printer_name(db)

        svc = CupsService(printer_name=queue)
        cups_job_id = svc.create_held_job(
            filepath=print_path,
            title=job.title,
            copies=job.copies,
            duplex=job.duplex,
            media=job.media,
        )
        job.cups_job_id = cups_job_id
        svc.release_job(cups_job_id)

        job.status = "printing"
        await db.commit()
        await log_event(db, "print.release", "print_job", str(job.id), user_id=user.id,
                        detail={"title": job.title, "copies": job.copies})
        await db.commit()
        await dispatch_webhook(db, "print.release", {"id": job.id, "title": job.title, "copies": job.copies})
        await ws_manager.broadcast("jobs", {
            "type": "job_updated", "data": {"id": job.id, "status": "printing"}
        })

    except Exception as e:
        job.status = "failed"
        job.error_message = str(e)
        await db.commit()
        await ws_manager.broadcast("jobs", {
            "type": "job_updated",
            "data": {"id": job.id, "status": "failed", "error": str(e)},
        })
        raise HTTPException(status_code=500, detail=str(e))

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

    if job.cups_job_id:
        # Try to cancel via CUPS — use the job's printer or default
        try:
            printer = await db.get(Printer, job.printer_id) if job.printer_id else None
            queue = f"{printer.cups_name}_release" if printer and not printer.is_network_queue else await get_default_printer_name(db)
            CupsService(printer_name=queue).cancel_job(job.cups_job_id)
        except Exception:
            pass

    job.status = "cancelled"
    await db.commit()
    try:
        await ws_manager.broadcast("jobs", {
            "type": "job_updated", "data": {"id": job.id, "status": "cancelled"}
        })
        await log_event(db, "print.cancel", "print_job", str(job.id), user_id=user.id,
                        detail={"title": job.title})
        await db.commit()
    except Exception:
        pass
    return job


@router.post("/bulk-delete", response_model=BulkDeleteResponse)
async def bulk_delete_jobs(
    body: BulkDeleteJobsRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete multiple print jobs and their files."""
    result = await db.execute(select(PrintJob).where(PrintJob.id.in_(body.ids)))
    jobs = result.scalars().all()

    deleted = 0
    for job in jobs:
        if job.cups_job_id and job.status in ("held", "printing"):
            try:
                printer = await db.get(Printer, job.printer_id) if job.printer_id else None
                queue = f"{printer.cups_name}_release" if printer and not printer.is_network_queue else await get_default_printer_name(db)
                CupsService(printer_name=queue).cancel_job(job.cups_job_id)
            except Exception:
                pass
        cleanup_file(job.filepath)
        await db.delete(job)
        deleted += 1

    await db.commit()

    for job_id in body.ids:
        await ws_manager.broadcast("jobs", {
            "type": "job_deleted", "data": {"id": job_id}
        })

    return BulkDeleteResponse(deleted=deleted)


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

    if job.cups_job_id and job.status in ("held", "printing"):
        try:
            printer = await db.get(Printer, job.printer_id) if job.printer_id else None
            queue = f"{printer.cups_name}_release" if printer and not printer.is_network_queue else await get_default_printer_name(db)
            CupsService(printer_name=queue).cancel_job(job.cups_job_id)
        except Exception:
            pass

    cleanup_file(job.filepath)
    job_id_copy = job.id
    title_copy = job.title
    await db.delete(job)
    await db.commit()

    try:
        await log_event(db, "print.delete", "print_job", str(job_id_copy), user_id=user.id,
                        detail={"title": title_copy})
        await db.commit()
        await dispatch_webhook(db, "print.delete", {"id": job_id_copy, "title": title_copy})
        await ws_manager.broadcast("jobs", {
            "type": "job_deleted", "data": {"id": job_id_copy}
        })
    except Exception:
        pass


@router.post("/{job_id}/reprint", response_model=PrintJobResponse, status_code=201)
async def reprint_job(
    job_id: int,
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

    default_printer = await get_default_printer(db)

    # Verify original printer still exists; fall back to default
    reprint_printer_id = None
    if original.printer_id:
        existing_printer = await db.get(Printer, original.printer_id)
        if existing_printer:
            reprint_printer_id = existing_printer.id
    if not reprint_printer_id and default_printer:
        reprint_printer_id = default_printer.id

    new_job = PrintJob(
        user_id=user.id,
        title=original.title,
        filename=original.filename,
        filepath=original.filepath,
        file_size=original.file_size,
        mime_type=original.mime_type,
        status="held",
        copies=original.copies,
        duplex=original.duplex,
        media=original.media,
        source_type="upload",
        printer_id=reprint_printer_id,
    )
    db.add(new_job)
    await db.commit()
    await db.refresh(new_job)

    await ws_manager.broadcast("jobs", {
        "type": "job_created",
        "data": {"id": new_job.id, "title": new_job.title, "status": "held", "source_type": "upload"},
    })

    return new_job
