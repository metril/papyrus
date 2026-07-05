"""Unit tests for the push-based printer-status watcher's change-detection.

`_poll_printer_statuses`/`_broadcast_changed_printer_statuses` in `app.main`
drive the 15s background task that pushes `printer_status` WS events. These
tests exercise the compare-and-broadcast step directly (no DB, no real CUPS
connection) with a fake CupsService returning changing statuses, asserting
the WS broadcast fires only when a printer's status actually changed.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.main as main_module


@pytest.fixture(autouse=True)
def _clear_previous_snapshot():
    """The previous-snapshot dict is module-level state; isolate each test."""
    main_module._printer_status_previous.clear()
    yield
    main_module._printer_status_previous.clear()


@pytest.fixture
def broadcast(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr("app.services.ws_manager.ws_manager.broadcast", mock)
    return mock


def _status(state: int) -> dict:
    return {
        "state": state,
        "state_message": "",
        "accepting_jobs": True,
        "markers": [],
        "state_reasons": [],
    }


# ---------------------------------------------------------------------------
# _broadcast_changed_printer_statuses: pure compare-and-broadcast step
# ---------------------------------------------------------------------------

async def test_broadcasts_on_first_snapshot(broadcast):
    previous: dict[str, dict] = {}
    payload = {"id": 1, "cups_name": "brother1", **_status(3)}
    current = {"brother1": payload}

    await main_module._broadcast_changed_printer_statuses(current, previous)

    broadcast.assert_awaited_once_with(
        "printers", {"type": "printer_status", "data": payload}
    )
    assert previous == current


async def test_no_broadcast_when_status_unchanged(broadcast):
    payload = {"id": 1, "cups_name": "brother1", **_status(3)}
    previous = {"brother1": dict(payload)}
    current = {"brother1": dict(payload)}

    await main_module._broadcast_changed_printer_statuses(current, previous)

    broadcast.assert_not_awaited()


async def test_broadcasts_only_for_the_changed_printer(broadcast):
    unchanged = {"id": 1, "cups_name": "brother1", **_status(3)}
    previous = {
        "brother1": dict(unchanged),
        "brother2": {"id": 2, "cups_name": "brother2", **_status(3)},
    }
    changed = {"id": 2, "cups_name": "brother2", **_status(5)}
    current = {
        "brother1": dict(unchanged),
        "brother2": changed,
    }

    await main_module._broadcast_changed_printer_statuses(current, previous)

    broadcast.assert_awaited_once_with(
        "printers", {"type": "printer_status", "data": changed}
    )
    assert previous == current


async def test_sequential_polls_broadcast_only_on_actual_changes(broadcast):
    """Simulates several poll cycles; broadcast should fire exactly once per
    real change, never for a repeated/unchanged status."""
    previous: dict[str, dict] = {}
    cycles = [
        {"id": 1, "cups_name": "brother1", **_status(3)},  # initial -> broadcast
        {"id": 1, "cups_name": "brother1", **_status(3)},  # unchanged -> no broadcast
        {"id": 1, "cups_name": "brother1", **_status(4)},  # changed -> broadcast
        {"id": 1, "cups_name": "brother1", **_status(4)},  # unchanged -> no broadcast
        {"id": 1, "cups_name": "brother1", **_status(3)},  # changed back -> broadcast
    ]

    for payload in cycles:
        await main_module._broadcast_changed_printer_statuses(
            {"brother1": payload}, previous
        )

    assert broadcast.await_count == 3


# ---------------------------------------------------------------------------
# _poll_printer_statuses: fake CupsService driving a full poll cycle
# ---------------------------------------------------------------------------

async def test_poll_fetches_status_per_printer_and_broadcasts_on_change(monkeypatch, broadcast):
    """A fake CupsService returns a changing status across two poll calls;
    the first poll broadcasts (new snapshot), the second doesn't (unchanged),
    and a third poll broadcasts again once the fake status changes."""
    fake_states = iter([3, 3, 4])

    class FakeCupsService:
        def __init__(self, printer_name: str):
            self.printer_name = printer_name

        async def get_printer_status(self) -> dict:
            return _status(next(fake_states))

    monkeypatch.setattr("app.services.cups_service.CupsService", FakeCupsService)

    printers = [SimpleNamespace(id=1, cups_name="brother1")]

    await main_module._poll_printer_statuses(printers)  # state 3 -> broadcast (new)
    await main_module._poll_printer_statuses(printers)  # state 3 -> no broadcast
    await main_module._poll_printer_statuses(printers)  # state 4 -> broadcast (changed)

    assert broadcast.await_count == 2


async def test_poll_skips_broadcast_when_no_physical_printers(monkeypatch, broadcast):
    class FakeCupsService:
        def __init__(self, printer_name: str):
            pass

        async def get_printer_status(self) -> dict:  # pragma: no cover - not called
            raise AssertionError("should not be called with an empty printer list")

    monkeypatch.setattr("app.services.cups_service.CupsService", FakeCupsService)

    await main_module._poll_printer_statuses([])

    broadcast.assert_not_awaited()


async def test_poll_survives_one_printer_erroring_and_still_broadcasts_others(
    monkeypatch, broadcast
):
    """Printer A's status fetch raises RuntimeError (e.g. cups.Connection()
    failing during a cupsd hiccup — not the IPPError CupsService maps to its
    fallback); printer B returns a changed status. The error must be confined
    to A: the cycle completes and B's change is still broadcast."""

    class FakeCupsService:
        def __init__(self, printer_name: str):
            self.printer_name = printer_name

        async def get_printer_status(self) -> dict:
            if self.printer_name == "brotherA":
                raise RuntimeError("cupsd connection refused")
            return _status(4)

    monkeypatch.setattr("app.services.cups_service.CupsService", FakeCupsService)

    printers = [
        SimpleNamespace(id=1, cups_name="brotherA"),
        SimpleNamespace(id=2, cups_name="brotherB"),
    ]
    # Seed previous snapshots for both so B's state 4 registers as a change
    # (and A's absence this cycle produces no broadcast of its own).
    main_module._printer_status_previous.update({
        "brotherA": {"id": 1, "cups_name": "brotherA", **_status(3)},
        "brotherB": {"id": 2, "cups_name": "brotherB", **_status(3)},
    })

    await main_module._poll_printer_statuses(printers)

    broadcast.assert_awaited_once_with(
        "printers",
        {"type": "printer_status", "data": {"id": 2, "cups_name": "brotherB", **_status(4)}},
    )
    # A is skipped for the cycle: no stale snapshot entry survives, so its
    # recovery next cycle is detected as a change and broadcast.
    assert "brotherA" not in main_module._printer_status_previous


async def test_erroring_printer_recovery_is_broadcast(monkeypatch, broadcast):
    """After a skipped (erroring) cycle, the printer's next successful status
    fetch is treated as a change and broadcast, even if the status matches
    what was last seen before the error."""
    behavior = {"fail": False}

    class FakeCupsService:
        def __init__(self, printer_name: str):
            self.printer_name = printer_name

        async def get_printer_status(self) -> dict:
            if behavior["fail"]:
                raise RuntimeError("cupsd hiccup")
            return _status(3)

    monkeypatch.setattr("app.services.cups_service.CupsService", FakeCupsService)
    printers = [SimpleNamespace(id=1, cups_name="brother1")]

    await main_module._poll_printer_statuses(printers)  # ok -> broadcast (new)
    behavior["fail"] = True
    await main_module._poll_printer_statuses(printers)  # error -> skipped, no broadcast
    behavior["fail"] = False
    await main_module._poll_printer_statuses(printers)  # recovered -> broadcast

    assert broadcast.await_count == 2
