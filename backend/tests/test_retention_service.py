"""``app.services.retention_service.cleanup_old_scans`` — F45: stale
"scanning" rows (a scan interrupted by a crash/restart, since only the
in-flight request/task ever transitions a row out of "scanning") are swept
up after an hour, independent of the normal completed/failed retention
window.
"""
from datetime import datetime, timedelta, timezone

from app.models import ScanJob
from app.services.retention_service import cleanup_old_scans


def _hours_ago(hours: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours)


async def test_old_completed_scan_is_deleted(db):
    scan = ScanJob(status="completed", created_at=_hours_ago(24 * 30))
    db.add(scan)
    await db.commit()
    await db.refresh(scan)

    deleted = await cleanup_old_scans(db, retention_days=7)

    assert deleted == 1
    assert await db.get(ScanJob, scan.id) is None


async def test_recent_completed_scan_is_kept(db):
    scan = ScanJob(status="completed", created_at=_hours_ago(1))
    db.add(scan)
    await db.commit()
    await db.refresh(scan)

    deleted = await cleanup_old_scans(db, retention_days=7)

    assert deleted == 0
    assert await db.get(ScanJob, scan.id) is not None


async def test_stale_scanning_row_older_than_an_hour_is_deleted(db):
    stuck = ScanJob(status="scanning", created_at=_hours_ago(2))
    db.add(stuck)
    await db.commit()
    await db.refresh(stuck)

    deleted = await cleanup_old_scans(db, retention_days=7)

    assert deleted == 1
    assert await db.get(ScanJob, stuck.id) is None


async def test_fresh_scanning_row_within_an_hour_is_kept(db):
    in_progress = ScanJob(status="scanning", created_at=_hours_ago(0.1))
    db.add(in_progress)
    await db.commit()
    await db.refresh(in_progress)

    deleted = await cleanup_old_scans(db, retention_days=7)

    assert deleted == 0
    assert await db.get(ScanJob, in_progress.id) is not None


async def test_stale_scanning_sweep_runs_even_when_retention_days_disabled(db):
    """retention_days<=0 disables the user-configurable completed/failed
    policy, but the stale-"scanning" sweep is a reliability cleanup, not a
    data-retention preference -- it must still run."""
    stuck = ScanJob(status="scanning", created_at=_hours_ago(2))
    old_completed = ScanJob(status="completed", created_at=_hours_ago(24 * 365))
    db.add_all([stuck, old_completed])
    await db.commit()
    await db.refresh(stuck)
    await db.refresh(old_completed)

    deleted = await cleanup_old_scans(db, retention_days=0)

    assert deleted == 1
    assert await db.get(ScanJob, stuck.id) is None
    # The completed/failed policy is genuinely off -- an old completed scan
    # survives when retention_days<=0.
    assert await db.get(ScanJob, old_completed.id) is not None
