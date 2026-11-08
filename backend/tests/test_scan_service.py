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
import os
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


class _TimingOutProcess:
    """First `wait()` call raises `asyncio.TimeoutError` -- mirrors exactly
    what `asyncio.wait_for` raises on a real timeout (test_system_health.py's
    established pattern), so a test doesn't need to wait one out for real.
    The second call (the kill-then-reap inside `_await_with_timeout`'s
    except block) succeeds normally, recording the reap."""

    def __init__(self, stderr=None):
        self.stderr = stderr if stderr is not None else _EmptyAsyncIter()
        self.killed = False
        self.waited = False
        self._wait_calls = 0

    def kill(self):
        self.killed = True

    async def wait(self):
        self._wait_calls += 1
        if self._wait_calls == 1:
            raise asyncio.TimeoutError()
        self.waited = True


# --------------------------------------------------------------------------- #
# F42: check_device availability logic
#
# `scanimage -L`'s real output line shape is `` device `NAME' is a DESC ``.
# For a `brother4:` (or other plain SANE) device, NAME is the exact stored
# device string. For an `airscan:` device, though, `probe_scanner_ip`'s
# eSCL-port-probing fallback stores `Scanner.device` as
# `airscan:{prefix}:{label}:{url}` (the URL is needed later to reconstruct
# airscan.conf), while `_write_airscan_device` registers the device in
# sane-airscan under just its label -- so `scanimage -L` actually prints
# `airscan:{prefix}:{label}`, with no URL suffix at all. A fixture that
# embeds the full URL-suffixed string in scanimage -L's output (as an
# earlier version of these tests did) verifies the matching logic against
# its own wrong assumption, not against a real scanimage -L line.
# --------------------------------------------------------------------------- #
async def test_check_device_available_when_airscan_device_matches_by_label(monkeypatch):
    proc = _FakeProcess()

    async def fake_communicate():
        return b"device `airscan:e:Brother DCP-L2540DW' is a eSCL scanner\n", b""

    proc.communicate = fake_communicate

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    result = await scan_service.check_device(
        device="airscan:e:Brother DCP-L2540DW:http://1.2.3.4/eSCL"
    )
    assert result["available"] is True


async def test_check_device_available_when_plain_device_matches_verbatim(monkeypatch):
    proc = _FakeProcess()

    async def fake_communicate():
        return b"device `brother4:net1;dev0' is a Brother scanner\n", b""

    proc.communicate = fake_communicate

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    result = await scan_service.check_device(device="brother4:net1;dev0")
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

    result = await scan_service.check_device(
        device="airscan:e:Brother DCP-L2540DW:http://1.2.3.4/eSCL"
    )
    assert result["available"] is False


async def test_check_device_unavailable_when_a_different_device_is_present(monkeypatch):
    """Negative case: the output lists a real, different scanner -- the
    configured device must not be reported available just because *some*
    device is present."""
    proc = _FakeProcess()

    async def fake_communicate():
        return b"device `airscan:e:Someone Elses Scanner' is a eSCL scanner\n", b""

    proc.communicate = fake_communicate

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    result = await scan_service.check_device(
        device="airscan:e:Brother DCP-L2540DW:http://1.2.3.4/eSCL"
    )
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


def _option(cmd, name):
    return cmd[cmd.index(name) + 1]


@pytest.mark.parametrize(
    "device, mode, expected_mode, expected_source",
    [
        ("brother4:net1;dev0", "Color", "24bit Color", "Automatic Document Feeder(left aligned)"),
        ("brother4:net1;dev0", "Lineart", "Black & White", "Automatic Document Feeder(left aligned)"),
        ("test:device", "Color", "Color", "ADF"),
    ],
)
async def test_scan_batch_uses_driver_option_names(
    tmp_path, monkeypatch, device, mode, expected_mode, expected_source
):
    """brscan4 rejects "Color" and "ADF", which failed every batch before a page fed."""
    commands = []

    async def fake_exec(*args, **kwargs):
        commands.append(args)
        return _FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(ScanError, match="No pages"):
        await scan_service.scan_batch(mode=mode, scan_dir=str(tmp_path), device=device)

    assert _option(commands[0], "--mode") == expected_mode
    assert _option(commands[0], "--source") == expected_source


async def test_scan_batch_passes_output_pattern_in_equals_form(tmp_path, monkeypatch):
    """scanimage rejects `--batch PATH` as "argument without option"."""
    commands = []

    async def fake_exec(*args, **kwargs):
        commands.append(args)
        return _FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(ScanError, match="No pages"):
        await scan_service.scan_batch(scan_dir=str(tmp_path), device="test:device")

    batch = [a for a in commands[0] if a.startswith("--batch")]
    assert len(batch) == 1
    assert batch[0].startswith("--batch=") and batch[0].endswith("page_%04d.tiff")


async def test_scan_adf_pdf_for_brscan4_uses_batch_with_driver_names(tmp_path, monkeypatch):
    commands = []

    async def fake_exec(*args, **kwargs):
        commands.append(args)
        return _FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(ScanError, match="No pages"):
        await scan_service.scan(
            fmt="pdf", source="ADF", scan_dir=str(tmp_path), device="brother4:net1;dev0"
        )

    assert any(a.startswith("--batch=") for a in commands[0])
    assert _option(commands[0], "--source") == "Automatic Document Feeder(left aligned)"


async def test_scan_adf_non_pdf_for_brscan4_refused_before_scanimage(tmp_path, monkeypatch):
    from app.exceptions import ScanRequestError

    commands = []

    async def fake_exec(*args, **kwargs):
        commands.append(args)
        return _FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(ScanRequestError, match="PDF"):
        await scan_service.scan(
            fmt="tiff", source="ADF", scan_dir=str(tmp_path), device="brother4:net1;dev0"
        )
    assert commands == []


async def test_scan_adf_for_brscan4_merges_every_page_and_counts(tmp_path, monkeypatch):
    from PIL import Image

    counts = []
    merged = []

    async def fake_exec(*args, **kwargs):
        if args[0] == "img2pdf":
            merged.append(args[1:-2])
            open(args[-1], "wb").write(b"%PDF")
            return _FakeProcess()
        pattern = next(a for a in args if a.startswith("--batch=")).split("=", 1)[1]
        for n in (1, 2, 3):
            Image.new("L", (4, 4)).save(pattern % n)
        return _FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    scan_id, path = await scan_service.scan(
        fmt="pdf", source="ADF", scan_dir=str(tmp_path), device="brother4:net1;dev0",
        page_count_callback=counts.append,
    )
    assert counts == [3] and len(merged[0]) == 3
    assert os.listdir(tmp_path) == [f"{scan_id}.pdf"]


async def test_sane_non_brother_adf_scan_unchanged(tmp_path, monkeypatch):
    commands = []

    async def fake_exec(*args, **kwargs):
        commands.append(args)
        return _FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(ScanError, match="no output"):
        await scan_service.scan(
            fmt="tiff", source="ADF", scan_dir=str(tmp_path), device="airscan:e:x"
        )
    assert not any(a.startswith("--batch") for a in commands[0])


async def test_scan_batch_logs_scanimage_error_when_no_pages(tmp_path, monkeypatch, caplog):
    class _Stderr(_EmptyAsyncIter):
        lines = [b"scanimage: setting of option --mode failed (Invalid argument)\n"]

        async def __anext__(self):
            if self.lines:
                return self.lines.pop(0)
            raise StopAsyncIteration

    async def fake_exec(*args, **kwargs):
        return _FakeProcess(returncode=1, stderr=_Stderr())

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    with caplog.at_level(logging.WARNING), pytest.raises(ScanError, match="No pages"):
        await scan_service.scan_batch(scan_dir=str(tmp_path), device="test:device")

    assert "setting of option --mode failed" in caplog.text


# --------------------------------------------------------------------------- #
# F41: scan_batch()'s two subprocesses (scanimage --batch, img2pdf) are
# timeout-bounded and cancellation-safe, exactly like scan()'s. A hung batch
# scan used to hold self._lock forever, wedging every subsequent scan (web
# UI and eSCL alike) with "Scanner is busy" until the process restarted.
# --------------------------------------------------------------------------- #
async def test_scan_batch_timeout_kills_subprocess_and_cleans_page_dir(tmp_path, monkeypatch):
    fixed_id = uuid_module.uuid4()
    monkeypatch.setattr(scan_service_module.uuid, "uuid4", lambda: fixed_id)
    page_dir = tmp_path / f"batch_{fixed_id}"

    proc = _TimingOutProcess()

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(ScanError, match="Batch scan timed out"):
        await scan_service.scan_batch(
            resolution=300, mode="Color", scan_dir=str(tmp_path), device="test:device"
        )

    assert proc.killed is True
    assert proc.waited is True
    assert not page_dir.exists()


async def test_scan_batch_cancelled_midflight_kills_subprocess_and_cleans_page_dir(
    tmp_path, monkeypatch
):
    fixed_id = uuid_module.uuid4()
    monkeypatch.setattr(scan_service_module.uuid, "uuid4", lambda: fixed_id)
    page_dir = tmp_path / f"batch_{fixed_id}"

    proc = _FakeProcess(stderr=_HangingAsyncIter())

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    task = asyncio.create_task(
        scan_service.scan_batch(
            resolution=300, mode="Color", scan_dir=str(tmp_path), device="test:device"
        )
    )
    await asyncio.sleep(0.05)  # let the task start and reach the stderr await
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert proc.killed is True
    assert proc.waited is True
    assert not page_dir.exists()


async def test_scan_batch_pdf_merge_timeout_kills_subprocess_and_cleans_page_dir(
    tmp_path, monkeypatch
):
    fixed_id = uuid_module.uuid4()
    monkeypatch.setattr(scan_service_module.uuid, "uuid4", lambda: fixed_id)
    page_dir = tmp_path / f"batch_{fixed_id}"
    pdf_proc = _TimingOutProcess()

    async def fake_exec(*args, **kwargs):
        if args[0] == "scanimage":
            # Simulate one page having been scanned before the ADF "ran out
            # of paper" (scanimage's expected non-zero exit in batch mode).
            page_dir.mkdir(parents=True, exist_ok=True)
            (page_dir / "page_0001.tiff").write_bytes(b"page-one")
            return _FakeProcess(returncode=1)
        return pdf_proc  # img2pdf hangs

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(ScanError, match="PDF merge timed out"):
        await scan_service.scan_batch(
            resolution=300, mode="Color", scan_dir=str(tmp_path), device="test:device"
        )

    assert pdf_proc.killed is True
    assert pdf_proc.waited is True
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


# --------------------------------------------------------------------------- #
# F5 legacy ruling: a scanner configured before secrets-at-rest was added (or
# whose "*set*" value hasn't been re-saved since) still holds a plaintext
# ftp_password -- delivery must keep working instead of raising InvalidToken
# and silently dropping the whole FTP action.
# --------------------------------------------------------------------------- #
async def test_run_post_scan_actions_ftp_delivers_legacy_plaintext_password(
    tmp_path, monkeypatch
):
    from app.services import ftp_service as ftp_service_module
    from app.services.crypto import decrypt_value

    captured: dict = {}

    async def fake_upload_ftp(
        host, port, username, password_encrypted, filepath, filename,
        remote_dir="/", use_tls=False,
    ):
        captured["password_encrypted"] = password_encrypted

    monkeypatch.setattr(ftp_service_module.ftp_service, "upload_ftp", fake_upload_ftp)

    scan_file = tmp_path / "scan.pdf"
    scan_file.write_bytes(b"%PDF-1.4 fake")

    scan_job = SimpleNamespace(
        filepath=str(scan_file),
        scan_id="ftp-legacy-plaintext-1",
        resolution=300,
        mode="Color",
        format="pdf",
        page_count=1,
        error_message=None,
    )
    scanner = SimpleNamespace(
        post_scan_config={
            "ftp_host": "ftp.example.com",
            "ftp_username": "scanuser",
            "ftp_password": "legacy-plaintext-secret",  # never Fernet-encrypted
        }
    )

    class _FakeDB:
        async def commit(self):
            pass

    await run_post_scan_actions(scan_job, scanner, _FakeDB())

    assert "password_encrypted" in captured
    # ftp_service's own (strict) decrypt_value must succeed -- proving the
    # legacy plaintext was normalized to a real Fernet token before it got
    # here -- and decrypt back to the exact original plaintext.
    assert decrypt_value(captured["password_encrypted"]) == "legacy-plaintext-secret"
    assert scan_job.error_message is None  # delivery succeeded, no failure recorded


def test_backend_for_returns_sane_backend():
    from app.services.scan_service import SaneBackend, ScanService

    svc = ScanService()
    assert isinstance(svc._backend_for("brother4:net1;dev0"), SaneBackend)
    assert isinstance(svc._backend_for("airscan:e0:x"), SaneBackend)


# --------------------------------------------------------------------------- #
# brother4 batches: non-zero exit only succeeds at the normal end of feed; the
# timeout resets on progress
# --------------------------------------------------------------------------- #
class _LinesStderr:
    def __init__(self, lines, delay=0.0):
        self._lines = list(lines)
        self._delay = delay

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._lines:
            raise StopAsyncIteration
        if self._delay:
            await asyncio.sleep(self._delay)
        return self._lines.pop(0)


def _batch_exec(monkeypatch, returncode, stderr, pages=2, hang=None):
    from PIL import Image

    async def fake_exec(*args, **kwargs):
        if args[0] == "img2pdf":
            open(args[-1], "wb").write(b"%PDF")
            return _FakeProcess()
        pattern = next(a for a in args if a.startswith("--batch=")).split("=", 1)[1]
        for n in range(1, pages + 1):
            Image.new("L", (4, 4)).save(pattern % n)
        return _FakeProcess(returncode=returncode, stderr=stderr)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)


async def test_brother4_batch_nonzero_without_end_of_feed_fails(tmp_path, monkeypatch, caplog):
    _batch_exec(monkeypatch, 1, _LinesStderr([b"scanimage: sane_read: Document feeder jammed\n"]))
    with caplog.at_level(logging.WARNING), pytest.raises(ScanError) as ei:
        await scan_service.scan_batch(scan_dir=str(tmp_path), device="brother4:net1;dev0")
    assert "jammed" not in ei.value.detail and "jammed" in caplog.text
    assert os.listdir(tmp_path) == []


async def test_brother4_batch_nonzero_with_end_of_feed_succeeds(tmp_path, monkeypatch):
    text = ("scanimage: sane_start: Document feeder OUT OF DOCUMENTS\n").encode()
    _batch_exec(monkeypatch, 1, _LinesStderr([text]))
    _, _, count = await scan_service.scan_batch(scan_dir=str(tmp_path), device="brother4:n")
    assert count == 2


async def test_brother4_batch_exit_zero_succeeds(tmp_path, monkeypatch):
    _batch_exec(monkeypatch, 0, _LinesStderr([]))
    _, _, count = await scan_service.scan_batch(scan_dir=str(tmp_path), device="brother4:n")
    assert count == 2


async def test_other_sane_batch_nonzero_still_succeeds(tmp_path, monkeypatch):
    _batch_exec(monkeypatch, 1, _LinesStderr([b"scanimage: whatever\n"]))
    _, _, count = await scan_service.scan_batch(scan_dir=str(tmp_path), device="airscan:e:x")
    assert count == 2


async def test_brother4_batch_stall_is_killed(tmp_path, monkeypatch):
    monkeypatch.setattr(scan_service_module, "_BATCH_NO_PROGRESS_SECONDS", 0.15)
    monkeypatch.setattr(scan_service_module, "_BATCH_POLL_SECONDS", 0.02)
    proc = _FakeProcess(stderr=_HangingAsyncIter())

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(ScanError, match="timed out"):
        await scan_service.scan_batch(scan_dir=str(tmp_path), device="brother4:n")
    assert proc.killed and proc.waited and os.listdir(tmp_path) == []


async def test_brother4_batch_progress_resets_the_stall_timer(tmp_path, monkeypatch):
    monkeypatch.setattr(scan_service_module, "_BATCH_NO_PROGRESS_SECONDS", 0.15)
    monkeypatch.setattr(scan_service_module, "_BATCH_POLL_SECONDS", 0.02)
    lines = [b"Progress: 10.0%\n"] * 8
    _batch_exec(monkeypatch, 0, _LinesStderr(lines, delay=0.05))
    _, _, count = await scan_service.scan_batch(scan_dir=str(tmp_path), device="brother4:n")
    assert count == 2


async def test_feeder_pdf_failure_after_merge_leaves_no_pdf(tmp_path, monkeypatch):
    fixed = uuid_module.uuid4()
    monkeypatch.setattr(scan_service_module.uuid, "uuid4", lambda: fixed)
    _batch_exec(monkeypatch, 0, _LinesStderr([]))

    real_rmtree = scan_service_module.shutil.rmtree
    calls = []

    def boom(path, ignore_errors=False):
        calls.append(path)
        if len(calls) == 1:
            raise OSError("disk")
        real_rmtree(path, ignore_errors=ignore_errors)

    monkeypatch.setattr(scan_service_module.shutil, "rmtree", boom)
    with pytest.raises(OSError):
        await scan_service.scan_batch(scan_dir=str(tmp_path), device="brother4:n")
    assert not (tmp_path / f"{fixed}.pdf").exists()
