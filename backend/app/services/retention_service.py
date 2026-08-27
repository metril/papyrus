"""Retention policy: auto-cleanup old scans, print jobs, and audit log rows."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditEntry, PrintJob, ScanJob
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

    F44: rows are deleted and committed *before* their files are unlinked.
    Unlinking first (the old order) meant a commit failure after 400 unlinks
    would leave 400 rows pointing at files that no longer exist -- every
    download/thumbnail for them 404s forever. Committing first means a
    failed/slow unlink can only strand an orphan file behind an already-
    deleted row, which is silently fixed by the next `save_upload_streaming`-
    style cleanup or just occupies disk space, never a broken row.

    Returns count deleted.
    """
    deleted = 0
    filepaths: list[str] = []

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
                filepaths.append(scan.filepath)
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
            filepaths.append(scan.filepath)
        await db.delete(scan)
        deleted += 1

    if deleted:
        await db.commit()
        for filepath in filepaths:
            await asyncio.to_thread(cleanup_file, filepath)
        logger.info("Retention: deleted %d old scans", deleted)

    return deleted


async def cleanup_old_print_jobs(db: AsyncSession, retention_days: int) -> int:
    """Delete completed/failed/cancelled print jobs older than retention_days.

    F44: see `cleanup_old_scans` -- rows are deleted and committed before
    their files are unlinked, not the other way around.
    """
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
    filepaths: list[str] = []
    for job in jobs:
        if job.filepath:
            filepaths.append(job.filepath)
        await db.delete(job)
        deleted += 1

    if deleted:
        await db.commit()
        for filepath in filepaths:
            await asyncio.to_thread(cleanup_file, filepath)
        logger.info(
            "Retention: deleted %d old print jobs (cutoff: %s)", deleted, cutoff.isoformat()
        )

    return deleted


async def cleanup_old_audit_log(db: AsyncSession, retention_days: int) -> int:
    """Delete audit_log rows older than retention_days (F69).

    Audit entries have no associated file, so there's no unlink phase here --
    delete + commit is the whole operation.
    """
    if retention_days <= 0:
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    result = await db.execute(select(AuditEntry).where(AuditEntry.created_at < cutoff))
    entries = result.scalars().all()

    deleted = 0
    for entry in entries:
        await db.delete(entry)
        deleted += 1

    if deleted:
        await db.commit()
        logger.info("Retention: deleted %d old audit log entries", deleted)

    return deleted


async def run_retention(
    db: AsyncSession, scan_days: int, print_days: int, audit_days: int = 90
) -> dict:
    """Run full retention cleanup."""
    scans_deleted = await cleanup_old_scans(db, scan_days)
    jobs_deleted = await cleanup_old_print_jobs(db, print_days)
    audit_entries_deleted = await cleanup_old_audit_log(db, audit_days)
    return {
        "scans_deleted": scans_deleted,
        "jobs_deleted": jobs_deleted,
        "audit_entries_deleted": audit_entries_deleted,
    }
