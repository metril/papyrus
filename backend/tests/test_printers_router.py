"""Tests for the printers router response helpers."""
from datetime import datetime, timezone

from app.models import Printer
from app.routers import printers as printers_router


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
