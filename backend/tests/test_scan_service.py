"""Unit tests for ``get_default_scanner_device``/``get_default_scanner``
(``app.services.scan_service``) — F35's "raise instead of returning an empty
device string" fix, and its settings-configured-device fallback.

``scan_service`` is a module-level singleton; ``_scanner_device`` is
monkeypatched per test so state never leaks between tests.
"""
from types import SimpleNamespace

import pytest

from app.exceptions import ScannerBusyError
from app.services.scan_service import get_default_scanner_device, scan_service


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
