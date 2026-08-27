"""Tests for the singular ``/api/printer`` router (``app.routers.printer``) --
the user-facing status/settings endpoints, as opposed to the admin CRUD
surface in ``app.routers.printers``.

Regression coverage for the F34 review finding: these endpoints used to
resolve the default printer's hold-queue name (``get_default_printer_name``),
a fake ``papyrus:/`` device with no PPD-backed markers/state, so the
dashboard's toner meter could disagree with what ``/api/printers`` and the
alert service already report from the real device (its ``_release`` queue).
"""
from app.models import Printer
from app.routers import printer as printer_router


class _FakeCupsService:
    """Records the queue name it was constructed with and the calls made."""

    last_printer_name: str | None = None

    def __init__(self, printer_name: str):
        _FakeCupsService.last_printer_name = printer_name

    async def get_printer_status(self) -> dict:
        return {
            "state": 3,
            "state_message": "Idle",
            "accepting_jobs": True,
            "markers": [],
            "state_reasons": [],
        }

    async def get_printer_options(self) -> dict:
        return {
            "media_supported": ["A4"],
            "media_default": "A4",
            "sides_supported": ["one-sided"],
            "color_supported": True,
        }


async def _seed_default_printer(db, cups_name="brother") -> Printer:
    printer = Printer(
        display_name="Brother", cups_name=cups_name, uri="ipp://x/ipp",
        is_default=True, is_network_queue=False,
    )
    db.add(printer)
    await db.commit()
    await db.refresh(printer)
    return printer


async def test_printer_status_queries_the_release_queue(db, user_client, monkeypatch):
    await _seed_default_printer(db, cups_name="brother")
    monkeypatch.setattr(printer_router, "CupsService", _FakeCupsService)

    resp = await user_client.get("/api/printer/status")
    assert resp.status_code == 200
    assert _FakeCupsService.last_printer_name == "brother_release"


async def test_printer_settings_queries_the_release_queue(db, user_client, monkeypatch):
    await _seed_default_printer(db, cups_name="brother")
    monkeypatch.setattr(printer_router, "CupsService", _FakeCupsService)

    resp = await user_client.get("/api/printer/settings")
    assert resp.status_code == 200
    assert _FakeCupsService.last_printer_name == "brother_release"


async def test_printer_status_no_default_printer_is_503(db, user_client):
    resp = await user_client.get("/api/printer/status")
    assert resp.status_code == 503
    assert resp.json()["detail"] == "No default printer configured"


async def test_printer_settings_no_default_printer_is_503(db, user_client):
    resp = await user_client.get("/api/printer/settings")
    assert resp.status_code == 503
    assert resp.json()["detail"] == "No default printer configured"
