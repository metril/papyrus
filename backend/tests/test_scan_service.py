"""Unit tests for ``app.services.scan_service``.

Covers ``get_default_scanner_device``/``get_default_scanner`` (F35's "raise
instead of returning an empty device string" fix, and its
settings-configured-device fallback), plus the F41/F42/F22/F110/F43/F21
fixes to ``check_device``/``scan``/``scan_batch``/``run_post_scan_actions``.

``scan_service`` is a module-level singleton shared across the whole test
session; every test either monkeypatches the specific attribute it needs
(auto-reverted) or drives ``scan()``/``scan_batch()`` to completion (success,
raise, or cancellation) so the shared ``asyncio.Lock`` is always released
before the test ends.

Real subprocesses are never spawned: ``asyncio.create_subprocess_exec`` is
monkeypatched per test, mirroring the convention in test_system_health.py
and test_convert_service.py.
"""
import asyncio
import logging
import uuid as uuid_module
from types import SimpleNamespace

import pytest

from app.exceptions import ScannerBusyError
from app.services import scan_service as scan_service_module
from app.services.scan_service import (
    ScanError,
    get_default_scanner_device,
    run_post_scan_actions,
    scan_service,
)


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalars(self._rows)


class _FakeDB:
    """Minimal AsyncSession stand-in: .execute() always returns the
    configured scanner row list, regardless of the query."""

    def __init__(self, scanners):
        self._scanners = scanners

    async def execute(self, _stmt):
        return _FakeResult(self._scanners)


def _scanner(device="airscan:e:Brother:http://1.2.3.4/eSCL"):
    return SimpleNamespace(device=device, is_default=True)


async def test_raises_when_no_default_row_and_no_settings_fallback(monkeypatch):
    monkeypatch.setattr(scan_service, "_scanner_device", "")
    db = _FakeDB([])

    with pytest.raises(ScannerBusyError) as exc_info:
        await get_default_scanner_device(db)
    assert exc_info.value.detail == "No default scanner configured"


async def test_returns_default_row_device_when_present(monkeypatch):
    monkeypatch.setattr(scan_service, "_scanner_device", "")
    db = _FakeDB([_scanner(device="airscan:e:Brother:http://1.2.3.4/eSCL")])

    result = await get_default_scanner_device(db)
    assert result == "airscan:e:Brother:http://1.2.3.4/eSCL"


async def test_falls_back_to_settings_configured_device_when_no_default_row(monkeypatch):
    # No default Scanner row, but scan_service was .configure()'d with a
    # legacy settings-based device -- must not raise.
    monkeypatch.setattr(scan_service, "_scanner_device", "brother4:net1;dev0")
    db = _FakeDB([])

    result = await get_default_scanner_device(db)
    assert result == "brother4:net1;dev0"


async def test_default_row_takes_priority_over_settings_fallback(monkeypatch):
    monkeypatch.setattr(scan_service, "_scanner_device", "brother4:net1;dev0")
    db = _FakeDB([_scanner(device="airscan:e:Brother:http://1.2.3.4/eSCL")])

    result = await get_default_scanner_device(db)
    assert result == "airscan:e:Brother:http://1.2.3.4/eSCL"


def test_is_busy_reflects_lock_state():
    assert scan_service.is_busy() is False


# --------------------------------------------------------------------------- #
# Fakes shared by the subprocess-driven tests below.
# --------------------------------------------------------------------------- #
class _EmptyAsyncIter:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _HangingAsyncIter:
    """Never yields before being cancelled -- simulates a scanimage child
    whose stderr never closes because it's still (or forever) running."""

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.sleep(3600)
        raise StopAsyncIteration  # pragma: no cover


class _FakeProcess:
    def __init__(self, returncode: int = 0, stderr=None):
        self.returncode = returncode
        self.stderr = stderr if stderr is not None else _EmptyAsyncIter()
        self.killed = False
        self.waited = False

    def kill(self):
        self.killed = True

    async def wait(self):
        self.waited = True


# --------------------------------------------------------------------------- #
# F42: check_device availability logic
# --------------------------------------------------------------------------- #
async def test_check_device_available_when_name_present_in_output(monkeypatch):
    proc = _FakeProcess()

    async def fake_communicate():
        return b"device `airscan:e:Brother:http://1.2.3.4/eSCL' is a Brother scanner\n", b""

    proc.communicate = fake_communicate

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    result = await scan_service.check_device(device="airscan:e:Brother:http://1.2.3.4/eSCL")
    assert result["available"] is True


async def test_check_device_unavailable_when_absent_even_if_returncode_zero(monkeypatch):
    """F42: `scanimage -L` exits 0 even when it finds nothing -- a bare
    `returncode == 0` check used to force "available" regardless."""
    proc = _FakeProcess(returncode=0)

    async def fake_communicate():
        return b"No scanners were identified.\n", b""

    proc.communicate = fake_communicate

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    result = await scan_service.check_device(device="airscan:e:Brother:http://1.2.3.4/eSCL")
    assert result["available"] is False


async def test_check_device_empty_device_is_never_available(monkeypatch):
    proc = _FakeProcess()

    async def fake_communicate():
        return b"device `airscan:e:Brother:http://1.2.3.4/eSCL' is a Brother scanner\n", b""

    proc.communicate = fake_communicate

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    result = await scan_service.check_device(device="")
    assert result["available"] is False


# --------------------------------------------------------------------------- #
# F41: check_device times out -> killed, awaited, and a domain error raised
# --------------------------------------------------------------------------- #
async def test_check_device_timeout_kills_awaits_and_raises(monkeypatch):
    proc = _FakeProcess()

    async def timing_out_communicate():
        raise asyncio.TimeoutError()

    proc.communicate = timing_out_communicate

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(ScannerBusyError):
        await scan_service.check_device(device="test:device")

    assert proc.killed is True
    assert proc.waited is True


# --------------------------------------------------------------------------- #
# F22: scan() cleans up the intermediate TIFF on every non-success exit
# --------------------------------------------------------------------------- #
async def test_scan_cleans_up_tiff_on_nonzero_returncode(tmp_path, monkeypatch):
    fixed_id = uuid_module.uuid4()
    monkeypatch.setattr(scan_service_module.uuid, "uuid4", lambda: fixed_id)
    tiff_path = tmp_path / f"{fixed_id}.tiff"
    tiff_path.write_bytes(b"partial-tiff-from-a-jammed-scan")

    async def fake_exec(*args, **kwargs):
        return _FakeProcess(returncode=1)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(ScanError):
        await scan_service.scan(
            resolution=300, mode="Color", fmt="pdf", scan_dir=str(tmp_path), device="test:device"
        )

    assert not tiff_path.exists()


# --------------------------------------------------------------------------- #
# F110: cancelling scan() kills the subprocess and cleans up (F22)
# --------------------------------------------------------------------------- #
async def test_scan_cancelled_midflight_kills_subprocess_and_cleans_tiff(tmp_path, monkeypatch):
    fixed_id = uuid_module.uuid4()
    monkeypatch.setattr(scan_service_module.uuid, "uuid4", lambda: fixed_id)
    tiff_path = tmp_path / f"{fixed_id}.tiff"
    tiff_path.write_bytes(b"partial-tiff-still-being-written")

    proc = _FakeProcess(stderr=_HangingAsyncIter())

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    task = asyncio.create_task(
        scan_service.scan(
            resolution=300, mode="Color", fmt="pdf", scan_dir=str(tmp_path), device="test:device"
        )
    )
    await asyncio.sleep(0.05)  # let the task start and reach the stderr await
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert proc.killed is True
    assert proc.waited is True
    assert not tiff_path.exists()


# --------------------------------------------------------------------------- #
# F43: scan()/scan_batch() use the passed scan_dir, not the singleton default
# --------------------------------------------------------------------------- #
async def test_scan_writes_to_the_passed_scan_dir(tmp_path, monkeypatch):
    fixed_id = uuid_module.uuid4()
    monkeypatch.setattr(scan_service_module.uuid, "uuid4", lambda: fixed_id)
    tiff_path = tmp_path / f"{fixed_id}.tiff"

    class _WritingProcess(_FakeProcess):
        async def wait(self):
            tiff_path.write_bytes(b"fake-tiff-bytes")
            self.waited = True

    async def fake_exec(*args, **kwargs):
        return _WritingProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    assert scan_service._scan_dir != str(tmp_path)

    scan_id, out_path = await scan_service.scan(
        resolution=300, mode="Color", fmt="tiff", scan_dir=str(tmp_path), device="test:device"
    )

    assert out_path == str(tiff_path)


async def test_scan_batch_cleans_up_page_dir_when_merge_fails(tmp_path, monkeypatch):
    """F22 for scan_batch(): a failed img2pdf merge must not leak page_dir
    and its (potentially hundreds of MB of) page TIFFs."""
    fixed_id = uuid_module.uuid4()
    monkeypatch.setattr(scan_service_module.uuid, "uuid4", lambda: fixed_id)
    page_dir = tmp_path / f"batch_{fixed_id}"

    async def fake_exec(*args, **kwargs):
        if args[0] == "scanimage":
            # Simulate one page having been scanned before the ADF "ran out
            # of paper" (scanimage's expected non-zero exit in batch mode).
            page_dir.mkdir(parents=True, exist_ok=True)
            (page_dir / "page_0001.tiff").write_bytes(b"page-one")
            return _FakeProcess(returncode=1)
        return _FakeProcess(returncode=1)  # img2pdf "failed"

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(ScanError, match="Failed to merge"):
        await scan_service.scan_batch(
            resolution=300, mode="Color", scan_dir=str(tmp_path), device="test:device"
        )

    assert not page_dir.exists()


# --------------------------------------------------------------------------- #
# F21: run_post_scan_actions logs and aggregates per-action failures
# --------------------------------------------------------------------------- #
async def test_run_post_scan_actions_logs_and_records_failed_action(tmp_path, caplog):
    class _FakeDB:
        def __init__(self):
            self.committed = False

        async def commit(self):
            self.committed = True

    scan_file = tmp_path / "scan.pdf"
    scan_file.write_bytes(b"%PDF-1.4 fake")

    scan_job = SimpleNamespace(
        filepath=str(scan_file),
        scan_id="12345678-aaaa-bbbb-cccc-000000000000",
        resolution=300,
        mode="Color",
        format="pdf",
        page_count=1,
        error_message=None,
    )
    # A folder target that doesn't exist -- shutil.copy2 raises, and nothing
    # else is configured so this is the only action attempted.
    scanner = SimpleNamespace(
        post_scan_config={"folder": str(tmp_path / "does-not-exist-as-a-directory")}
    )
    db = _FakeDB()

    with caplog.at_level(logging.WARNING, logger="app.services.scan_service"):
        await run_post_scan_actions(scan_job, scanner, db)

    assert scan_job.error_message == "Delivery failed: folder"
    assert db.committed is True
    assert any(
        "folder" in record.message and scan_job.scan_id in record.message
        for record in caplog.records
    )


async def test_run_post_scan_actions_no_config_is_a_silent_noop(tmp_path):
    """No post_scan_config at all -- nothing to do, no error recorded."""
    scan_job = SimpleNamespace(filepath=str(tmp_path / "scan.pdf"), error_message=None)
    scanner = SimpleNamespace(post_scan_config=None)

    await run_post_scan_actions(scan_job, scanner, db=None)

    assert scan_job.error_message is None
