"""Tests for the eSCL in-memory `_scan_jobs` TTL eviction.

`_scan_jobs` is a module-level dict (eSCL jobs are transient, not
DB-backed) that used to grow unboundedly whenever an eSCL client fetched a
completed job's document but never issued the final `DELETE
/ScanJobs/{id}` (or a job failed and the client never came back). Eviction
must only ever remove jobs in a terminal state (`Completed`/`Canceled`)
whose `terminal_at` stamp is older than the TTL — a job still `Pending` or
`Processing` must never be evicted, no matter how old it is.

Time is driven through a monkeypatched `escl.time.monotonic`, matching the
convention used for the settings/CUPS-status TTL caches.
"""
import pytest

from app.routers import escl


@pytest.fixture(autouse=True)
def _clear_scan_jobs():
    """Module-level dict; isolate each test."""
    escl._scan_jobs.clear()
    yield
    escl._scan_jobs.clear()


def _job(state: str, terminal_at: float | None) -> dict:
    return {
        "state": state,
        "resolution": 300,
        "color_mode": "Color",
        "format": "pdf",
        "source": "Flatbed",
        "scan_region": {},
        "filepath": None,
        "served": False,
        "error": None,
        "terminal_at": terminal_at,
    }


# ---------------------------------------------------------------------------
# _purge_stale_jobs in isolation
# ---------------------------------------------------------------------------


def test_purge_evicts_completed_job_older_than_ttl(monkeypatch):
    fake_now = [10_000.0]
    monkeypatch.setattr(escl.time, "monotonic", lambda: fake_now[0])

    escl._scan_jobs["old-completed"] = _job("Completed", terminal_at=fake_now[0])

    fake_now[0] += escl._JOB_TTL_SECONDS + 1
    escl._purge_stale_jobs()

    assert "old-completed" not in escl._scan_jobs


def test_purge_evicts_canceled_job_older_than_ttl(monkeypatch):
    fake_now = [10_000.0]
    monkeypatch.setattr(escl.time, "monotonic", lambda: fake_now[0])

    escl._scan_jobs["old-canceled"] = _job("Canceled", terminal_at=fake_now[0])

    fake_now[0] += escl._JOB_TTL_SECONDS + 1
    escl._purge_stale_jobs()

    assert "old-canceled" not in escl._scan_jobs


def test_purge_keeps_fresh_completed_job(monkeypatch):
    fake_now = [10_000.0]
    monkeypatch.setattr(escl.time, "monotonic", lambda: fake_now[0])

    escl._scan_jobs["fresh-completed"] = _job("Completed", terminal_at=fake_now[0])

    fake_now[0] += escl._JOB_TTL_SECONDS - 1
    escl._purge_stale_jobs()

    assert "fresh-completed" in escl._scan_jobs


def test_purge_never_evicts_processing_job_regardless_of_age(monkeypatch):
    fake_now = [10_000.0]
    monkeypatch.setattr(escl.time, "monotonic", lambda: fake_now[0])

    # Even if a Processing job somehow carried an ancient terminal_at stamp
    # (should never happen in practice), the state check alone must protect
    # it — an in-progress job is never a candidate for eviction.
    escl._scan_jobs["stuck-processing"] = _job("Processing", terminal_at=fake_now[0])

    fake_now[0] += escl._JOB_TTL_SECONDS * 100
    escl._purge_stale_jobs()

    assert "stuck-processing" in escl._scan_jobs


def test_purge_never_evicts_pending_job_regardless_of_age(monkeypatch):
    fake_now = [10_000.0]
    monkeypatch.setattr(escl.time, "monotonic", lambda: fake_now[0])

    escl._scan_jobs["stuck-pending"] = _job("Pending", terminal_at=None)

    fake_now[0] += escl._JOB_TTL_SECONDS * 100
    escl._purge_stale_jobs()

    assert "stuck-pending" in escl._scan_jobs


def test_purge_keeps_terminal_job_missing_a_stamp(monkeypatch):
    """Defensive: a terminal job with no `terminal_at` (should not occur once
    both transition sites stamp it) is left alone rather than evicted, since
    there's no age to compare against."""
    fake_now = [10_000.0]
    monkeypatch.setattr(escl.time, "monotonic", lambda: fake_now[0])

    escl._scan_jobs["unstamped"] = _job("Completed", terminal_at=None)

    fake_now[0] += escl._JOB_TTL_SECONDS * 100
    escl._purge_stale_jobs()

    assert "unstamped" in escl._scan_jobs


def test_purge_leaves_unrelated_jobs_alone(monkeypatch):
    fake_now = [10_000.0]
    monkeypatch.setattr(escl.time, "monotonic", lambda: fake_now[0])

    escl._scan_jobs["old-completed"] = _job("Completed", terminal_at=fake_now[0])
    escl._scan_jobs["active"] = _job("Processing", terminal_at=None)

    fake_now[0] += escl._JOB_TTL_SECONDS + 1
    escl._purge_stale_jobs()

    assert "old-completed" not in escl._scan_jobs
    assert "active" in escl._scan_jobs


# ---------------------------------------------------------------------------
# Wiring: endpoints purge opportunistically on access/mutation
# ---------------------------------------------------------------------------


async def test_get_next_document_purges_stale_siblings_before_serving(monkeypatch, tmp_path):
    fake_now = [10_000.0]
    monkeypatch.setattr(escl.time, "monotonic", lambda: fake_now[0])

    stale = _job("Completed", terminal_at=fake_now[0])
    escl._scan_jobs["stale-sibling"] = stale

    fake_now[0] += escl._JOB_TTL_SECONDS + 1

    # Target completes *after* the sibling has already gone stale, so it must
    # survive the purge triggered by this same request.
    scan_file = tmp_path / "scan.pdf"
    scan_file.write_bytes(b"%PDF-1.4 fake")
    target = _job("Completed", terminal_at=fake_now[0])
    target["filepath"] = str(scan_file)
    escl._scan_jobs["target"] = target

    response = await escl.get_next_document("target")

    assert "stale-sibling" not in escl._scan_jobs
    assert response.status_code == 200
    assert escl._scan_jobs["target"]["served"] is True


async def test_cancel_scan_job_purges_stale_siblings(monkeypatch):
    fake_now = [10_000.0]
    monkeypatch.setattr(escl.time, "monotonic", lambda: fake_now[0])

    escl._scan_jobs["stale-sibling"] = _job("Canceled", terminal_at=fake_now[0])
    escl._scan_jobs["to-cancel"] = _job("Pending", terminal_at=None)

    fake_now[0] += escl._JOB_TTL_SECONDS + 1

    response = await escl.cancel_scan_job("to-cancel")

    assert response.status_code == 200
    assert "stale-sibling" not in escl._scan_jobs
    assert "to-cancel" not in escl._scan_jobs  # popped by the cancel itself
