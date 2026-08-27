"""Tests for CopyService.copy.

F10: the service used to print via the module-level `cups_service` singleton
(printer_name="") and scan via the module-level `scan_service` singleton
(scanner device never configured on the copy path), so every copy failed.
The caller (routers/copy.py) now resolves a real CupsService + scanner device
from the DB and passes both in.

Ruling 1: `device` flows through to `scan_service.scan(device=...)` rather
than relying on the (never-configured) scan_service singleton default.
"""
import pytest

from app.services import copy_service as copy_service_module
from app.services.copy_service import CopyError, CopyService
from app.services.scan_service import ScanError


class _FakeCups:
    def __init__(self):
        self.created: list[tuple] = []
        self.released: list[int] = []

    async def create_held_job(self, filepath, title, copies=1, duplex=False, media="A4"):
        self.created.append((filepath, title, copies, duplex, media))
        return 42

    async def release_job(self, job_id):
        self.released.append(job_id)


class _FailingCreateCups(_FakeCups):
    async def create_held_job(self, *args, **kwargs):
        raise RuntimeError("printer offline")


class _FakeScanService:
    """Stand-in for the module-level scan_service singleton."""

    def __init__(self, filepath: str = "/tmp/scan-fake.tiff"):
        self.filepath = filepath
        self.scan_calls: list[dict] = []

    async def scan(
        self, resolution, mode, fmt, source, progress_callback=None, device=None,
        scan_dir=None,
    ):
        self.scan_calls.append({
            "resolution": resolution, "mode": mode, "fmt": fmt,
            "source": source, "device": device, "scan_dir": scan_dir,
        })
        return "scan-123", self.filepath


class _FailingScanService:
    async def scan(self, *args, **kwargs):
        raise ScanError("scanner offline")


async def test_copy_passes_device_through_to_scan_service(monkeypatch):
    fake_scan = _FakeScanService()
    monkeypatch.setattr(copy_service_module, "scan_service", fake_scan)

    svc = CopyService()
    fake_cups = _FakeCups()

    result = await svc.copy(
        cups=fake_cups, device="airscan:e0:MyScanner", scan_dir="/mnt/nas/scans"
    )

    assert fake_scan.scan_calls == [{
        "resolution": 300, "mode": "Color", "fmt": "tiff",
        "source": "Flatbed", "device": "airscan:e0:MyScanner", "scan_dir": "/mnt/nas/scans",
    }]
    assert result == {
        "scan_id": "scan-123",
        "cups_job_id": 42,
        "filepath": fake_scan.filepath,
    }
    assert fake_cups.created == [(fake_scan.filepath, "Copy_scan-123", 1, False, "A4")]
    assert fake_cups.released == [42]


async def test_copy_uses_the_provided_cups_instance_not_a_singleton(monkeypatch):
    """Regression (F10): copy used to print through the module-level
    `cups_service` singleton (printer_name=""), which always failed. It must
    print through whichever CupsService instance the caller passes in."""
    fake_scan = _FakeScanService()
    monkeypatch.setattr(copy_service_module, "scan_service", fake_scan)
    svc = CopyService()
    fake_cups_a = _FakeCups()
    fake_cups_b = _FakeCups()

    await svc.copy(cups=fake_cups_a, device="dev0")

    assert fake_cups_a.created
    assert not fake_cups_b.created  # the other instance was never touched


async def test_copy_scan_failure_raises_copy_error(monkeypatch):
    monkeypatch.setattr(copy_service_module, "scan_service", _FailingScanService())

    svc = CopyService()
    with pytest.raises(CopyError, match="Scan failed"):
        await svc.copy(cups=_FakeCups(), device="dev0")


async def test_copy_print_failure_raises_copy_error(monkeypatch):
    fake_scan = _FakeScanService()
    monkeypatch.setattr(copy_service_module, "scan_service", fake_scan)

    svc = CopyService()
    with pytest.raises(CopyError, match="Print failed"):
        await svc.copy(cups=_FailingCreateCups(), device="dev0")
