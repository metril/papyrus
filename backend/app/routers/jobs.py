import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_permission
from app.database import get_db
from app.models import PrintJob, User
from app.schemas import PrintJobList, PrintJobResponse
from app.services.convert_service import convert_to_pdf, is_printable, needs_conversion
from app.services.cups_service import cups_service
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

    # Save uploaded file
    upload_path = get_upload_path(file.filename)
    content = await file.read()

    if not validate_upload_size(len(content)):
        raise HTTPException(status_code=413, detail="File too large")

    with open(upload_path, "wb") as f:
        f.write(content)

    # Create job record
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
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # If not holding, process immediately
    if not hold:
        await _process_job(job, db)

    return job


async def _process_job(job: PrintJob, db: AsyncSession):
    """Convert (if needed) and send job to CUPS."""
    print_path = job.filepath

    try:
        # Convert office documents to PDF
        if needs_conversion(job.mime_type):
            job.status = "converting"
            await db.commit()
            await ws_manager.broadcast("jobs", {
                "type": "job_updated", "data": {"id": job.id, "status": "converting"}
            })

            output_dir = os.path.dirname(job.filepath)
            print_path = await convert_to_pdf(job.filepath, output_dir)

        # Send to CUPS
        job.status = "printing"
        await db.commit()
        await ws_manager.broadcast("jobs", {
            "type": "job_updated", "data": {"id": job.id, "status": "printing"}
        })

        cups_job_id = cups_service.create_held_job(
            filepath=print_path,
            title=job.title,
            copies=job.copies,
            duplex=job.duplex,
            media=job.media,
        )
        job.cups_job_id = cups_job_id

        # Release immediately since hold=False
        cups_service.release_job(cups_job_id)

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


@router.post("/{job_id}/release", response_model=PrintJobResponse)
async def release_job(
    job_id: int,
    user: User = Depends(require_permission("print")),
    db: AsyncSession = Depends(get_db),
):
    """Release a held job to start printing."""
    result = await db.execute(select(PrintJob).where(PrintJob.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "held":
        raise HTTPException(status_code=400, detail=f"Job is not held (status: {job.status})")

    try:
        # Convert if needed
        print_path = job.filepath
        if needs_conversion(job.mime_type):
            job.status = "converting"
            await db.commit()
            await ws_manager.broadcast("jobs", {
                "type": "job_updated", "data": {"id": job.id, "status": "converting"}
            })
            output_dir = os.path.dirname(job.filepath)
            print_path = await convert_to_pdf(job.filepath, output_dir)

        # Send to CUPS and release
        cups_job_id = cups_service.create_held_job(
            filepath=print_path,
            title=job.title,
            copies=job.copies,
            duplex=job.duplex,
            media=job.media,
        )
        job.cups_job_id = cups_job_id
        cups_service.release_job(cups_job_id)

        job.status = "printing"
        await db.commit()
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
        try:
            cups_service.cancel_job(job.cups_job_id)
        except Exception:
            pass

    job.status = "cancelled"
    await db.commit()
    await ws_manager.broadcast("jobs", {
        "type": "job_updated", "data": {"id": job.id, "status": "cancelled"}
    })
    return job


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

    # Cancel CUPS job if active
    if job.cups_job_id and job.status in ("held", "printing"):
        try:
            cups_service.cancel_job(job.cups_job_id)
        except Exception:
            pass

    # Clean up file
    cleanup_file(job.filepath)

    await db.delete(job)
    await db.commit()
