"""``app.services.retention_service`` — F45: stale "scanning" rows (a scan
interrupted by a crash/restart, since only the in-flight request/task ever
transitions a row out of "scanning") are swept up after an hour, independent
of the normal completed/failed retention window. F44/F69 additions below
cover print-job/audit-log cleanup and the delete-before-unlink ordering.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import AuditEntry, PrintJob, ScanJob
from app.services import retention_service
from app.services.retention_service import (
    cleanup_old_audit_log,
    cleanup_old_print_jobs,
    cleanup_old_scans,
    run_retention,
)


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


# --------------------------------------------------------------------------- #
# F44 — rows are deleted and committed *before* their files are unlinked, so
# a slow/failing unlink can never leave a committed row pointing at a file
# that no longer exists.
# --------------------------------------------------------------------------- #
async def test_cleanup_old_scans_commits_before_unlinking_file(db, tmp_path, monkeypatch):
    filepath = tmp_path / "scan.pdf"
    filepath.write_bytes(b"fake pdf")
    scan = ScanJob(status="completed", filepath=str(filepath), created_at=_hours_ago(24 * 30))
    db.add(scan)
    await db.commit()
    await db.refresh(scan)

    original_commit = db.commit
    commit_happened = False

    async def spy_commit():
        nonlocal commit_happened
        commit_happened = True
        await original_commit()

    monkeypatch.setattr(db, "commit", spy_commit)

    unlinked: list[str] = []

    def spy_cleanup_file(path):
        assert commit_happened, "file was unlinked before the row's delete committed"
        unlinked.append(path)

    monkeypatch.setattr(retention_service, "cleanup_file", spy_cleanup_file)

    deleted = await cleanup_old_scans(db, retention_days=7)

    assert deleted == 1
    assert unlinked == [str(filepath)]


async def test_cleanup_old_print_jobs_commits_before_unlinking_file(db, tmp_path, monkeypatch):
    filepath = tmp_path / "job.pdf"
    filepath.write_bytes(b"fake pdf")
    job = PrintJob(
        title="job.pdf",
        filename="job.pdf",
        filepath=str(filepath),
        file_size=8,
        mime_type="application/pdf",
        status="completed",
        created_at=_hours_ago(24 * 60),
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    original_commit = db.commit
    commit_happened = False

    async def spy_commit():
        nonlocal commit_happened
        commit_happened = True
        await original_commit()

    monkeypatch.setattr(db, "commit", spy_commit)

    unlinked: list[str] = []

    def spy_cleanup_file(path):
        assert commit_happened, "file was unlinked before the row's delete committed"
        unlinked.append(path)

    monkeypatch.setattr(retention_service, "cleanup_file", spy_cleanup_file)

    deleted = await cleanup_old_print_jobs(db, retention_days=30)

    assert deleted == 1
    assert unlinked == [str(filepath)]


# --------------------------------------------------------------------------- #
# cleanup_old_print_jobs — no prior coverage existed for this function at all.
# --------------------------------------------------------------------------- #
async def test_old_completed_print_job_is_deleted(db, tmp_path):
    filepath = tmp_path / "job.pdf"
    filepath.write_bytes(b"fake pdf")
    job = PrintJob(
        title="job.pdf",
        filename="job.pdf",
        filepath=str(filepath),
        file_size=8,
        mime_type="application/pdf",
        status="completed",
        created_at=_hours_ago(24 * 60),
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    deleted = await cleanup_old_print_jobs(db, retention_days=30)

    assert deleted == 1
    assert not filepath.exists()
    result = await db.execute(select(PrintJob).where(PrintJob.id == job.id))
    assert result.scalar_one_or_none() is None


async def test_recent_print_job_is_kept(db, tmp_path):
    filepath = tmp_path / "job.pdf"
    filepath.write_bytes(b"fake pdf")
    job = PrintJob(
        title="job.pdf",
        filename="job.pdf",
        filepath=str(filepath),
        file_size=8,
        mime_type="application/pdf",
        status="completed",
        created_at=_hours_ago(1),
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    deleted = await cleanup_old_print_jobs(db, retention_days=30)

    assert deleted == 0
    assert filepath.exists()


async def test_print_job_cleanup_disabled_when_retention_days_not_positive(db, tmp_path):
    job = PrintJob(
        title="job.pdf",
        filename="job.pdf",
        filepath=str(tmp_path / "job.pdf"),
        file_size=8,
        mime_type="application/pdf",
        status="completed",
        created_at=_hours_ago(24 * 365),
    )
    db.add(job)
    await db.commit()

    deleted = await cleanup_old_print_jobs(db, retention_days=0)

    assert deleted == 0


# --------------------------------------------------------------------------- #
# F69 — audit_log rows are pruned like scans/print jobs.
# --------------------------------------------------------------------------- #
async def test_old_audit_log_entry_is_deleted(db):
    entry = AuditEntry(action="print.release", created_at=_hours_ago(24 * 100))
    db.add(entry)
    await db.commit()
    await db.refresh(entry)

    deleted = await cleanup_old_audit_log(db, retention_days=90)

    assert deleted == 1
    result = await db.execute(select(AuditEntry).where(AuditEntry.id == entry.id))
    assert result.scalar_one_or_none() is None


async def test_recent_audit_log_entry_is_kept(db):
    entry = AuditEntry(action="print.release", created_at=_hours_ago(1))
    db.add(entry)
    await db.commit()
    await db.refresh(entry)

    deleted = await cleanup_old_audit_log(db, retention_days=90)

    assert deleted == 0
    result = await db.execute(select(AuditEntry).where(AuditEntry.id == entry.id))
    assert result.scalar_one_or_none() is not None


async def test_audit_log_cleanup_disabled_when_retention_days_not_positive(db):
    entry = AuditEntry(action="print.release", created_at=_hours_ago(24 * 1000))
    db.add(entry)
    await db.commit()

    deleted = await cleanup_old_audit_log(db, retention_days=0)

    assert deleted == 0


# --------------------------------------------------------------------------- #
# run_retention — aggregates all three sweeps.
# --------------------------------------------------------------------------- #
async def test_run_retention_aggregates_scans_jobs_and_audit_counts(db, tmp_path):
    scan = ScanJob(status="completed", created_at=_hours_ago(24 * 30))
    job = PrintJob(
        title="job.pdf",
        filename="job.pdf",
        filepath=str(tmp_path / "nonexistent.pdf"),
        file_size=8,
        mime_type="application/pdf",
        status="completed",
        created_at=_hours_ago(24 * 60),
    )
    entry = AuditEntry(action="print.release", created_at=_hours_ago(24 * 100))
    db.add_all([scan, job, entry])
    await db.commit()

    result = await run_retention(db, scan_days=7, print_days=30, audit_days=90)

    assert result == {"scans_deleted": 1, "jobs_deleted": 1, "audit_entries_deleted": 1}
