"""``main._reconcile_on_startup`` — F23 (self-heal the airscan.conf entry
for every airscan: scanner on every boot, since /etc/sane.d isn't a
persisted volume) and F45 (mark leftover "scanning" ScanJob rows failed,
since only the in-flight request/task that created one ever transitions it
out of that status -- a crash/restart otherwise strands it forever).

CUPS/printer reconciliation is monkeypatched to a no-op so this focuses on
the scanner-config and ScanJob pieces; ``cups`` itself is already stubbed as
a MagicMock by conftest, and ``_list_existing_cups``'s own
except-Exception-empty-set fallback absorbs iterating it, but
``cups_admin.ensure_default_queue`` is monkeypatched too for a clean,
fast, deterministic run.
"""
from app.database import async_session
from app.main import _reconcile_on_startup
from app.models import ScanJob, Scanner
from app.routers import scanners as scanners_router
from app.services import cups_admin


async def _neutralize_cups(monkeypatch):
    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(cups_admin, "ensure_default_queue", _noop)


async def test_airscan_scanner_config_is_restored_on_startup(db, monkeypatch):
    await _neutralize_cups(monkeypatch)

    calls: list[tuple] = []

    def _fake_ensure(name, device, post_scan_config):
        calls.append((name, device, post_scan_config))

    monkeypatch.setattr(scanners_router, "_ensure_airscan_config", _fake_ensure)

    scanner = Scanner(
        name="Brother",
        device="airscan:e:Brother:http://1.2.3.4/eSCL",
        post_scan_config={"airscan_url": "http://1.2.3.4/eSCL", "airscan_protocol": "eSCL"},
    )
    db.add(scanner)
    await db.commit()

    await _reconcile_on_startup()

    assert calls == [
        (
            "Brother",
            "airscan:e:Brother:http://1.2.3.4/eSCL",
            {"airscan_url": "http://1.2.3.4/eSCL", "airscan_protocol": "eSCL"},
        )
    ]


async def test_non_airscan_scanner_is_not_passed_to_ensure_airscan_config(db, monkeypatch):
    await _neutralize_cups(monkeypatch)

    calls: list[tuple] = []
    monkeypatch.setattr(
        scanners_router, "_ensure_airscan_config", lambda *a, **kw: calls.append(a)
    )

    scanner = Scanner(name="Brother4", device="brother4:net1;dev0")
    db.add(scanner)
    await db.commit()

    await _reconcile_on_startup()

    assert calls == []


async def test_ensure_airscan_config_failure_does_not_abort_reconcile(db, monkeypatch):
    """A failure writing one scanner's airscan.conf entry must not stop the
    rest of startup reconciliation (matches every other step's
    except-log-continue pattern)."""
    await _neutralize_cups(monkeypatch)

    def _raise(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(scanners_router, "_ensure_airscan_config", _raise)

    scanner = Scanner(name="Brother", device="airscan:e:Brother:http://1.2.3.4/eSCL")
    scanning_job = ScanJob(status="scanning")
    db.add_all([scanner, scanning_job])
    await db.commit()
    await db.refresh(scanning_job)

    # Must not raise.
    await _reconcile_on_startup()

    # The rest of reconcile (the ScanJob sweep, F45) still ran. Read back
    # through a fresh session rather than the `db` fixture's -- reconcile
    # opens/closes several of its own sessions against the same pool, and
    # reusing the fixture's session afterward is flaky under asyncpg/greenlet.
    async with async_session() as fresh:
        refreshed = await fresh.get(ScanJob, scanning_job.id)
        assert refreshed.status == "failed"


async def test_stale_scanning_scan_job_marked_failed_on_startup(db, monkeypatch):
    await _neutralize_cups(monkeypatch)

    scanning_job = ScanJob(status="scanning")
    completed_job = ScanJob(status="completed")
    db.add_all([scanning_job, completed_job])
    await db.commit()
    await db.refresh(scanning_job)
    await db.refresh(completed_job)

    await _reconcile_on_startup()

    async with async_session() as fresh:
        refreshed_scanning = await fresh.get(ScanJob, scanning_job.id)
        refreshed_completed = await fresh.get(ScanJob, completed_job.id)

        assert refreshed_scanning.status == "failed"
        assert refreshed_scanning.error_message == "Interrupted by server restart"
        # A row that already reached a terminal status is left untouched.
        assert refreshed_completed.status == "completed"
        assert refreshed_completed.error_message is None


async def test_no_stale_scanning_rows_is_a_noop(db, monkeypatch):
    await _neutralize_cups(monkeypatch)

    # Must not raise with an empty scan_jobs table.
    await _reconcile_on_startup()
