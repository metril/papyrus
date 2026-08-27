"""Retention policy: auto-cleanup old scans and print jobs."""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PrintJob, ScanJob
from app.services.file_service import cleanup_file

logger = logging.getLogger(__name__)


_STALE_SCANNING_HOURS = 1


async def cleanup_old_scans(db: AsyncSession, retention_days: int) -> int:
    """Delete completed/failed scans older than retention_days, plus any
    scan stuck in "scanning" for over an hour (F45).

    Rows are inserted with status="scanning" and only ever transitioned by
    the in-flight request/task that created them -- a crash or restart mid-
    scan strands the row there permanently, where it's invisible to the
    completed/failed filter below and never gets cleaned up. That sweep is
    unconditional (not gated on retention_days > 0, unlike the normal
    completed/failed policy) since it's a reliability cleanup, not a
    user-configurable data-retention window.

    Returns count deleted.
    """
    deleted = 0

    if retention_days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        result = await db.execute(
            select(ScanJob).where(
                ScanJob.status.in_(["completed", "failed"]),
                ScanJob.created_at < cutoff,
            )
        )
        for scan in result.scalars().all():
            if scan.filepath:
                cleanup_file(scan.filepath)
            await db.delete(scan)
            deleted += 1

    stale_cutoff = datetime.now(timezone.utc) - timedelta(hours=_STALE_SCANNING_HOURS)
    result = await db.execute(
        select(ScanJob).where(
            ScanJob.status == "scanning",
            ScanJob.created_at < stale_cutoff,
        )
    )
    for scan in result.scalars().all():
        if scan.filepath:
            cleanup_file(scan.filepath)
        await db.delete(scan)
        deleted += 1

    if deleted:
        await db.commit()
        logger.info("Retention: deleted %d old scans", deleted)

    return deleted


async def cleanup_old_print_jobs(db: AsyncSession, retention_days: int) -> int:
    """Delete completed/failed/cancelled print jobs older than retention_days."""
    if retention_days <= 0:
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    result = await db.execute(
        select(PrintJob).where(
            PrintJob.status.in_(["completed", "failed", "cancelled"]),
            PrintJob.created_at < cutoff,
        )
    )
    jobs = result.scalars().all()

    deleted = 0
    for job in jobs:
        if job.filepath:
            cleanup_file(job.filepath)
        await db.delete(job)
        deleted += 1

    if deleted:
        await db.commit()
        logger.info(
            "Retention: deleted %d old print jobs (cutoff: %s)", deleted, cutoff.isoformat()
        )

    return deleted


async def run_retention(db: AsyncSession, scan_days: int, print_days: int) -> dict:
    """Run full retention cleanup."""
    scans_deleted = await cleanup_old_scans(db, scan_days)
    jobs_deleted = await cleanup_old_print_jobs(db, print_days)
    return {"scans_deleted": scans_deleted, "jobs_deleted": jobs_deleted}
