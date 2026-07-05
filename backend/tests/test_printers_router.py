"""Tests for the printers router response helpers and discovery/probe/refresh
endpoints.

Following this codebase's router-test convention (see
test_scanner_thumbnail_endpoint.py), endpoint functions are called directly
with fake AsyncSession stand-ins rather than spinning up a TestClient. No
real network I/O happens: ``discover_printers``, ``probe_ipp``, and the
router's own ``_check_reachable`` TCP check are all monkeypatched.
"""
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.models import Printer, PrintJob, User
from app.routers import printers as printers_router


class _FakeScalars:
    def __init__(self, items):
        self._items = items

    def __iter__(self):
        return iter(self._items)


class _FakeQueryResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return _FakeScalars(self._items)

    def scalar_one_or_none(self):
        return self._items[0] if self._items else None


class _FakeDB:
    """Minimal AsyncSession stand-in: `.execute()` always returns the
    configured `printers` list, `.get()` returns a fixed printer (or None),
    and add/commit/refresh are no-ops that record whether they ran."""

    def __init__(self, printers=None, get_return=None):
        self.printers = printers or []
        self.get_return = get_return
        self.committed = 0
        self.added = None
        self.refreshed = None

    async def execute(self, _query):
        return _FakeQueryResult(self.printers)

    async def get(self, _model, _id):
        return self.get_return

    def add(self, obj):
        self.added = obj

    async def commit(self):
        self.committed += 1

    async def refresh(self, obj):
        self.refreshed = obj


async def _fake_cups_status(_cups_name: str) -> dict:
    return {"state": 3, "state_message": "Idle", "accepting_jobs": True}


async def test_printer_response_includes_device_info_fields(monkeypatch):
    status = {"state": 3, "state_message": "Idle", "accepting_jobs": True}

    async def fake_cups_status(cups_name: str) -> dict:
        assert cups_name == "brother"
        return status

    monkeypatch.setattr(printers_router, "_cups_status", fake_cups_status)

    printer = Printer(
        id=1,
        display_name="Brother",
        cups_name="brother",
        uri="ipp://192.168.1.50/ipp/print",
        description="Office printer",
        make_and_model="Brother DCP-L2540DW",
        location="Upstairs office",
        is_default=True,
        is_network_queue=False,
        auto_release=False,
        created_at=datetime(2026, 7, 5, tzinfo=timezone.utc),
    )

    resp = await printers_router._printer_response(printer)

    assert resp["make_and_model"] == "Brother DCP-L2540DW"
    assert resp["location"] == "Upstairs office"
    assert resp["cups_status"] == status
    assert resp["id"] == 1
    assert resp["display_name"] == "Brother"
    assert resp["uri"] == "ipp://192.168.1.50/ipp/print"


async def test_printer_response_device_info_defaults_to_none(monkeypatch):
    async def fake_cups_status(cups_name: str) -> dict:
        return {"state": 5, "state_message": "Unavailable", "accepting_jobs": False}

    monkeypatch.setattr(printers_router, "_cups_status", fake_cups_status)

    printer = Printer(
        id=2,
        display_name="Plain",
        cups_name="plain",
        uri="",
        created_at=datetime(2026, 7, 5, tzinfo=timezone.utc),
    )

    resp = await printers_router._printer_response(printer)

    assert resp["make_and_model"] is None
    assert resp["location"] is None


# --------------------------------------------------------------------------- #
# GET /printers/discover
# --------------------------------------------------------------------------- #
def _device(name: str, ip: str, uri: str) -> dict:
    return {
        "name": name,
        "ip": ip,
        "port": 631,
        "make_model": None,
        "location": None,
        "uri": uri,
        "uuid": None,
        "protocols": ["ipp"],
    }


async def test_discover_marks_already_configured_by_exact_host_match(monkeypatch):
    devices = [
        # Device ip equals the configured uri's host exactly -> flagged.
        _device("Office Brother", "192.168.1.50", "ipp://192.168.1.50:631/ipp/print"),
        # Unrelated device -> not flagged.
        _device("New Printer", "192.168.1.99", "ipp://192.168.1.99:631/ipp/print"),
    ]

    async def fake_discover(timeout: float = 4.0):
        return devices

    monkeypatch.setattr(printers_router, "discover_printers", fake_discover)

    configured = Printer(
        id=1,
        display_name="Brother",
        cups_name="brother",
        uri="ipp://192.168.1.50/ipp/print",
        created_at=datetime(2026, 7, 5, tzinfo=timezone.utc),
    )
    db = _FakeDB(printers=[configured])

    result = await printers_router.discover_network_printers(db=db, _user=None)

    printers = result["printers"]
    assert printers[0]["already_configured"] is True
    assert printers[1]["already_configured"] is False


async def test_discover_ip_prefix_of_configured_host_is_not_configured(monkeypatch):
    # Adversarial case for substring matching: "10.0.0.1" is a literal
    # substring of the configured uri "ipp://10.0.0.11/ipp", but it is a
    # *different* device and must not be hidden as "Already added".
    devices = [
        _device("Prefix Device", "10.0.0.1", "ipp://10.0.0.1:631/ipp/print"),
        _device("Configured Device", "10.0.0.11", "ipp://10.0.0.11:631/ipp/print"),
    ]

    async def fake_discover(timeout: float = 4.0):
        return devices

    monkeypatch.setattr(printers_router, "discover_printers", fake_discover)

    configured = Printer(
        id=1,
        display_name="Eleven",
        cups_name="eleven",
        uri="ipp://10.0.0.11/ipp",
        created_at=datetime(2026, 7, 5, tzinfo=timezone.utc),
    )
    db = _FakeDB(printers=[configured])

    result = await printers_router.discover_network_printers(db=db, _user=None)

    printers = result["printers"]
    assert printers[0]["already_configured"] is False
    assert printers[1]["already_configured"] is True


async def test_discover_skips_malformed_configured_uris(monkeypatch):
    # A malformed stored uri (urlparse raises ValueError on it) must not
    # break discover or affect matching of other configured printers.
    devices = [
        _device("Known", "192.168.1.50", "ipp://192.168.1.50:631/ipp/print"),
        _device("Unknown", "192.168.1.99", "ipp://192.168.1.99:631/ipp/print"),
    ]

    async def fake_discover(timeout: float = 4.0):
        return devices

    monkeypatch.setattr(printers_router, "discover_printers", fake_discover)

    malformed = Printer(
        id=1,
        display_name="Broken",
        cups_name="broken",
        uri="ipp://[invalid/ipp",
        created_at=datetime(2026, 7, 5, tzinfo=timezone.utc),
    )
    configured = Printer(
        id=2,
        display_name="Brother",
        cups_name="brother",
        uri="ipp://192.168.1.50/ipp",
        created_at=datetime(2026, 7, 5, tzinfo=timezone.utc),
    )
    db = _FakeDB(printers=[malformed, configured])

    result = await printers_router.discover_network_printers(db=db, _user=None)

    printers = result["printers"]
    assert printers[0]["already_configured"] is True
    assert printers[1]["already_configured"] is False


async def test_discover_marks_already_configured_by_uri_equality(monkeypatch):
    # The device's own ip is *not* a substring of the configured uri (which
    # uses a hostname), so this can only match via exact uri equality.
    device = {
        "name": "By Hostname",
        "ip": "192.168.1.77",
        "port": 631,
        "make_model": None,
        "location": None,
        "uri": "ipp://printer.local:631/ipp/print",
        "uuid": None,
        "protocols": ["ipp"],
    }

    async def fake_discover(timeout: float = 4.0):
        return [device]

    monkeypatch.setattr(printers_router, "discover_printers", fake_discover)

    configured = Printer(
        id=2,
        display_name="ByHost",
        cups_name="byhost",
        uri="ipp://printer.local:631/ipp/print",
        created_at=datetime(2026, 7, 5, tzinfo=timezone.utc),
    )
    db = _FakeDB(printers=[configured])

    result = await printers_router.discover_network_printers(db=db, _user=None)

    assert result["printers"][0]["already_configured"] is True


# --------------------------------------------------------------------------- #
# GET /printers/probe
# --------------------------------------------------------------------------- #
async def test_probe_unreachable_returns_nulls_and_fallback_uri(monkeypatch):
    async def fake_unreachable(ip: str) -> bool:
        return False

    monkeypatch.setattr(printers_router, "_check_reachable", fake_unreachable)

    result = await printers_router.probe_printer_ip(ip="192.168.1.50", _user=None)

    assert result == {
        "reachable": False,
        "uri": "ipp://192.168.1.50/ipp",
        "make_model": None,
        "location": None,
        "state": None,
        "suggested_display_name": None,
    }


async def test_probe_reachable_and_enriched_returns_corrected_uri(monkeypatch):
    async def fake_reachable(ip: str) -> bool:
        return True

    async def fake_probe_ipp(host: str, port: int = 631, timeout: float = 5.0):
        return {
            "make_and_model": "Brother DCP-L2540DW",
            "location": "Office",
            "state": 3,
            "resource": "/ipp/print",
        }

    monkeypatch.setattr(printers_router, "_check_reachable", fake_reachable)
    monkeypatch.setattr(printers_router, "probe_ipp", fake_probe_ipp)

    result = await printers_router.probe_printer_ip(ip="192.168.1.50", _user=None)

    assert result == {
        "reachable": True,
        "uri": "ipp://192.168.1.50:631/ipp/print",
        "make_model": "Brother DCP-L2540DW",
        "location": "Office",
        "state": 3,
        "suggested_display_name": "Brother DCP-L2540DW",
    }


async def test_probe_reachable_but_enrichment_none_returns_fallback(monkeypatch):
    async def fake_reachable(ip: str) -> bool:
        return True

    async def fake_probe_ipp(host: str, port: int = 631, timeout: float = 5.0):
        return None

    monkeypatch.setattr(printers_router, "_check_reachable", fake_reachable)
    monkeypatch.setattr(printers_router, "probe_ipp", fake_probe_ipp)

    result = await printers_router.probe_printer_ip(ip="192.168.1.50", _user=None)

    assert result == {
        "reachable": True,
        "uri": "ipp://192.168.1.50/ipp",
        "make_model": None,
        "location": None,
        "state": None,
        "suggested_display_name": None,
    }


# --------------------------------------------------------------------------- #
# POST /printers/{id}/refresh-info
# --------------------------------------------------------------------------- #
async def test_refresh_info_404_when_missing():
    db = _FakeDB(get_return=None)

    with pytest.raises(HTTPException) as exc_info:
        await printers_router.refresh_printer_info(printer_id=999, db=db, _user=None)

    assert exc_info.value.status_code == 404


async def test_refresh_info_updates_fields_on_success(monkeypatch):
    monkeypatch.setattr(printers_router, "_cups_status", _fake_cups_status)

    async def fake_probe_ipp(host: str, port: int = 631, timeout: float = 5.0):
        assert host == "192.168.1.50"
        return {
            "make_and_model": "Brother DCP-L2540DW",
            "location": "Office",
            "resource": "/ipp/print",
        }

    monkeypatch.setattr(printers_router, "probe_ipp", fake_probe_ipp)

    printer = Printer(
        id=5,
        display_name="Brother",
        cups_name="brother",
        uri="ipp://192.168.1.50:631/ipp/print",
        created_at=datetime(2026, 7, 5, tzinfo=timezone.utc),
    )
    db = _FakeDB(get_return=printer)

    result = await printers_router.refresh_printer_info(printer_id=5, db=db, _user=None)

    assert printer.make_and_model == "Brother DCP-L2540DW"
    assert printer.location == "Office"
    assert db.committed == 1
    assert result["make_and_model"] == "Brother DCP-L2540DW"
    assert result["location"] == "Office"


async def test_refresh_info_leaves_fields_untouched_on_probe_failure(monkeypatch):
    monkeypatch.setattr(printers_router, "_cups_status", _fake_cups_status)

    async def fake_probe_ipp(host: str, port: int = 631, timeout: float = 5.0):
        return None

    monkeypatch.setattr(printers_router, "probe_ipp", fake_probe_ipp)

    printer = Printer(
        id=6,
        display_name="Brother",
        cups_name="brother",
        uri="ipp://192.168.1.50:631/ipp/print",
        make_and_model="Existing Model",
        location="Existing Location",
        created_at=datetime(2026, 7, 5, tzinfo=timezone.utc),
    )
    db = _FakeDB(get_return=printer)

    result = await printers_router.refresh_printer_info(printer_id=6, db=db, _user=None)

    assert printer.make_and_model == "Existing Model"
    assert printer.location == "Existing Location"
    assert db.committed == 0
    assert result["make_and_model"] == "Existing Model"


# --------------------------------------------------------------------------- #
# POST /printers (enrichment-on-add)
# --------------------------------------------------------------------------- #
async def test_add_printer_with_failing_enrichment_still_succeeds(monkeypatch):
    monkeypatch.setattr(printers_router, "_cups_status", _fake_cups_status)

    async def fake_add_physical_printer(cups_name: str, display_name: str, uri: str) -> None:
        return None

    monkeypatch.setattr(
        printers_router.cups_admin, "add_physical_printer", fake_add_physical_printer
    )

    async def fake_probe_ipp(host: str, port: int = 631, timeout: float = 5.0):
        raise RuntimeError("simulated enrichment failure")

    monkeypatch.setattr(printers_router, "probe_ipp", fake_probe_ipp)

    db = _FakeDB(printers=[])
    body = printers_router.PrinterCreate(
        display_name="Brother",
        uri="ipp://192.168.1.50/ipp/print",
        is_network_queue=False,
    )

    result = await printers_router.add_printer(body=body, db=db, _user=None)

    assert result["display_name"] == "Brother"
    assert result["make_and_model"] is None
    assert result["location"] is None


async def test_add_printer_with_successful_enrichment_populates_fields(monkeypatch):
    monkeypatch.setattr(printers_router, "_cups_status", _fake_cups_status)

    async def fake_add_physical_printer(cups_name: str, display_name: str, uri: str) -> None:
        return None

    monkeypatch.setattr(
        printers_router.cups_admin, "add_physical_printer", fake_add_physical_printer
    )

    async def fake_probe_ipp(host: str, port: int = 631, timeout: float = 5.0):
        return {
            "make_and_model": "Brother DCP-L2540DW",
            "location": "Office",
            "resource": "/ipp/print",
        }

    monkeypatch.setattr(printers_router, "probe_ipp", fake_probe_ipp)

    db = _FakeDB(printers=[])
    body = printers_router.PrinterCreate(
        display_name="Brother",
        uri="ipp://192.168.1.50/ipp/print",
        is_network_queue=False,
    )

    result = await printers_router.add_printer(body=body, db=db, _user=None)

    assert result["make_and_model"] == "Brother DCP-L2540DW"
    assert result["location"] == "Office"
    assert db.committed == 2  # initial insert + post-enrichment update


async def test_add_printer_with_malformed_uri_still_succeeds(monkeypatch):
    # A URI with unmatched IPv6 brackets makes urlparse() itself raise
    # ValueError (not just probe_ipp) -- enrichment must swallow that too.
    monkeypatch.setattr(printers_router, "_cups_status", _fake_cups_status)

    async def fake_add_physical_printer(cups_name: str, display_name: str, uri: str) -> None:
        return None

    monkeypatch.setattr(
        printers_router.cups_admin, "add_physical_printer", fake_add_physical_printer
    )

    db = _FakeDB(printers=[])
    body = printers_router.PrinterCreate(
        display_name="Weird",
        uri="ipp://[invalid/ipp",
        is_network_queue=False,
    )

    result = await printers_router.add_printer(body=body, db=db, _user=None)

    assert result["make_and_model"] is None
    assert result["location"] is None


# --------------------------------------------------------------------------- #
# POST /printers/{id}/test-page
# --------------------------------------------------------------------------- #
def _admin_user() -> User:
    return User(id=uuid.uuid4(), email="admin@example.com", display_name="Admin", role="admin")


async def test_test_page_404_when_printer_missing():
    db = _FakeDB(get_return=None)

    with pytest.raises(HTTPException) as exc_info:
        await printers_router.send_test_page(printer_id=999, db=db, user=_admin_user())

    assert exc_info.value.status_code == 404


async def test_test_page_400_for_network_queue():
    printer = Printer(
        id=3,
        display_name="Hold Queue",
        cups_name="hold",
        uri="",
        is_network_queue=True,
        created_at=datetime(2026, 7, 5, tzinfo=timezone.utc),
    )
    db = _FakeDB(get_return=printer)

    with pytest.raises(HTTPException) as exc_info:
        await printers_router.send_test_page(printer_id=3, db=db, user=_admin_user())

    assert exc_info.value.status_code == 400


async def test_test_page_success_returns_serialized_job(monkeypatch):
    printer = Printer(
        id=4,
        display_name="Brother",
        cups_name="brother",
        uri="ipp://192.168.1.50/ipp/print",
        is_network_queue=False,
        created_at=datetime(2026, 7, 5, tzinfo=timezone.utc),
    )
    db = _FakeDB(get_return=printer)

    job = PrintJob(
        id=42,
        title="Test page — Brother",
        filename="test-page.pdf",
        filepath="/app/data/uploads/abc_test-page.pdf",
        file_size=1234,
        mime_type="application/pdf",
        status="printing",
        copies=1,
        duplex=False,
        media="A4",
        source_type="test_page",
        printer_id=4,
        cups_job_id=555,
        created_at=datetime(2026, 7, 5, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 5, tzinfo=timezone.utc),
    )

    async def fake_print_test_page(_db, _printer, _user):
        return job

    monkeypatch.setattr(printers_router, "print_test_page", fake_print_test_page)

    result = await printers_router.send_test_page(printer_id=4, db=db, user=_admin_user())

    assert result["id"] == 42
    assert result["status"] == "printing"
    assert result["cups_job_id"] == 555
    assert result["source_type"] == "test_page"


async def test_test_page_cups_failure_returns_502(monkeypatch):
    printer = Printer(
        id=5,
        display_name="Brother",
        cups_name="brother",
        uri="ipp://192.168.1.50/ipp/print",
        is_network_queue=False,
        created_at=datetime(2026, 7, 5, tzinfo=timezone.utc),
    )
    db = _FakeDB(get_return=printer)

    async def fake_print_test_page(_db, _printer, _user):
        raise printers_router.TestPageError("printer offline")

    monkeypatch.setattr(printers_router, "print_test_page", fake_print_test_page)

    with pytest.raises(HTTPException) as exc_info:
        await printers_router.send_test_page(printer_id=5, db=db, user=_admin_user())

    assert exc_info.value.status_code == 502
    assert "printer offline" in exc_info.value.detail


async def test_add_network_queue_printer_skips_enrichment(monkeypatch):
    monkeypatch.setattr(printers_router, "_cups_status", _fake_cups_status)

    async def fake_add_network_queue(cups_name: str, display_name: str) -> None:
        return None

    monkeypatch.setattr(printers_router.cups_admin, "add_network_queue", fake_add_network_queue)

    async def fake_probe_ipp(host: str, port: int = 631, timeout: float = 5.0):
        raise AssertionError("probe_ipp should not be called for network queues")

    monkeypatch.setattr(printers_router, "probe_ipp", fake_probe_ipp)

    db = _FakeDB(printers=[])
    body = printers_router.PrinterCreate(
        display_name="Network Hold Queue",
        is_network_queue=True,
    )

    result = await printers_router.add_printer(body=body, db=db, _user=None)

    assert result["make_and_model"] is None
    assert db.committed == 1
