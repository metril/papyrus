"""Printers API suite — CRUD, discovery, IPP probing, and test-page printing
through the real HTTP surface.

Complements (does not replace) test_printers_router.py's direct-call unit
tests. Faked at the exact same boundaries and the exact same attribute names
those tests already monkeypatch on ``app.routers.printers`` — ``_cups_status``,
``discover_printers``, ``_local_ipv4_addresses``, ``_check_reachable``,
``probe_ipp``, ``cups_admin.*``, ``print_test_page`` — just exercised through
HTTP requests instead of calling the router functions directly.
"""
from datetime import datetime, timezone

from app.models import Printer, PrintJob
from app.routers import printers as printers_router
from app.services.test_page_service import TestPageError


async def _fake_cups_status(cups_name: str) -> dict:
    return {"state": 3, "state_message": "Idle", "accepting_jobs": True}


def _patch_cups_status(monkeypatch) -> None:
    monkeypatch.setattr(printers_router, "_cups_status", _fake_cups_status)


def _device(name: str, ip: str, port: int, uri: str) -> dict:
    return {
        "name": name,
        "ip": ip,
        "port": port,
        "make_model": None,
        "location": None,
        "uri": uri,
        "uuid": None,
        "protocols": ["ipp"],
    }


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #
async def test_add_printer_creates_row_and_calls_cups_admin(db, admin_client, monkeypatch):
    _patch_cups_status(monkeypatch)
    calls = []

    async def fake_add_physical_printer(cups_name, display_name, uri):
        calls.append((cups_name, display_name, uri))

    monkeypatch.setattr(
        printers_router.cups_admin, "add_physical_printer", fake_add_physical_printer
    )

    resp = await admin_client.post(
        "/api/printers",
        json={"display_name": "Brother", "uri": "", "is_network_queue": False},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["display_name"] == "Brother"
    assert body["cups_name"] == "Brother"
    assert calls == [("Brother", "Brother", "")]


async def test_add_printer_duplicate_cups_name_is_409(db, admin_client, monkeypatch):
    _patch_cups_status(monkeypatch)

    async def fake_add_physical_printer(cups_name, display_name, uri):
        return None

    monkeypatch.setattr(
        printers_router.cups_admin, "add_physical_printer", fake_add_physical_printer
    )

    body = {"display_name": "Brother", "uri": "", "is_network_queue": False}
    first = await admin_client.post("/api/printers", json=body)
    assert first.status_code == 201

    second = await admin_client.post("/api/printers", json=body)
    assert second.status_code == 409


async def test_list_printers_includes_created_printer(db, admin_client, monkeypatch):
    _patch_cups_status(monkeypatch)
    printer = Printer(display_name="Brother", cups_name="brother", uri="")
    db.add(printer)
    await db.commit()

    resp = await admin_client.get("/api/printers")
    assert resp.status_code == 200
    names = [p["display_name"] for p in resp.json()]
    assert "Brother" in names


async def test_update_printer_calls_cups_admin_on_uri_change(db, admin_client, monkeypatch):
    _patch_cups_status(monkeypatch)
    printer = Printer(display_name="Brother", cups_name="brother", uri="ipp://old/ipp")
    db.add(printer)
    await db.commit()
    await db.refresh(printer)

    calls = []

    async def fake_update(cups_name, display_name, new_uri):
        calls.append((cups_name, display_name, new_uri))

    monkeypatch.setattr(printers_router.cups_admin, "update_physical_printer", fake_update)

    resp = await admin_client.patch(f"/api/printers/{printer.id}", json={"uri": "ipp://new/ipp"})
    assert resp.status_code == 200
    assert resp.json()["uri"] == "ipp://new/ipp"
    assert calls == [("brother", "Brother", "ipp://new/ipp")]


async def test_set_default_enforces_single_default(db, admin_client, monkeypatch):
    _patch_cups_status(monkeypatch)
    p1 = Printer(display_name="One", cups_name="one", uri="", is_default=True)
    p2 = Printer(display_name="Two", cups_name="two", uri="")
    db.add_all([p1, p2])
    await db.commit()
    await db.refresh(p1)
    await db.refresh(p2)

    resp = await admin_client.post(f"/api/printers/{p2.id}/default")
    assert resp.status_code == 200
    assert resp.json()["is_default"] is True

    # p1 and p2 were loaded in this fixture's session before the request (a
    # separate session) flipped p1.is_default -> False; roll back to drop the
    # stale snapshot before re-reading it. Capture the scalar id first --
    # rollback expires every attribute on p1/p2, and `p1.id` itself would need
    # a lazy (sync-context) reload if read after the rollback.
    p1_id = p1.id
    await db.rollback()
    refreshed_p1 = await db.get(Printer, p1_id)
    assert refreshed_p1.is_default is False


async def test_delete_printer_removes_row_and_calls_remove_printer(db, admin_client, monkeypatch):
    _patch_cups_status(monkeypatch)
    printer = Printer(display_name="Gone", cups_name="gone", uri="")
    db.add(printer)
    await db.commit()
    await db.refresh(printer)
    printer_id = printer.id

    calls = []

    async def fake_remove(cups_name):
        calls.append(cups_name)

    monkeypatch.setattr(printers_router.cups_admin, "remove_printer", fake_remove)

    resp = await admin_client.delete(f"/api/printers/{printer_id}")
    assert resp.status_code == 204
    assert calls == ["gone"]

    listing = await admin_client.get("/api/printers")
    assert all(p["id"] != printer_id for p in listing.json())


# --------------------------------------------------------------------------- #
# GET /printers/discover
# --------------------------------------------------------------------------- #
async def test_discover_filters_self_advertisement_and_flags_configured(
    db, admin_client, monkeypatch
):
    configured = Printer(display_name="Known", cups_name="known", uri="ipp://192.168.1.50/ipp")
    db.add(configured)
    await db.commit()

    devices = [
        # Papyrus's own static mDNS advertisement -- must never reach the response.
        _device("Papyrus @ host", "192.168.1.5", 6310, "ipp://192.168.1.5:6310/printers/Papyrus"),
        _device("Known Printer", "192.168.1.50", 631, "ipp://192.168.1.50:631/ipp/print"),
        _device("New Printer", "192.168.1.99", 631, "ipp://192.168.1.99:631/ipp/print"),
    ]

    async def fake_discover(timeout: float = 4.0):
        return devices

    monkeypatch.setattr(printers_router, "discover_printers", fake_discover)
    # Force the fingerprint-fallback filter path (deterministic regardless of
    # the test host's real network interfaces), same as the existing unit test.
    monkeypatch.setattr(printers_router, "_local_ipv4_addresses", lambda: None)

    resp = await admin_client.get("/api/printers/discover")
    assert resp.status_code == 200
    printers = resp.json()["printers"]
    names = [p["name"] for p in printers]
    assert "Papyrus @ host" not in names

    by_name = {p["name"]: p for p in printers}
    assert by_name["Known Printer"]["already_configured"] is True
    assert by_name["New Printer"]["already_configured"] is False


# --------------------------------------------------------------------------- #
# GET /printers/probe
# --------------------------------------------------------------------------- #
async def test_probe_reachable_and_enriched_returns_corrected_uri(admin_client, monkeypatch):
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

    resp = await admin_client.get("/api/printers/probe", params={"ip": "192.168.1.50"})
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "reachable": True,
        "uri": "ipp://192.168.1.50:631/ipp/print",
        "make_model": "Brother DCP-L2540DW",
        "location": "Office",
        "state": 3,
        "suggested_display_name": "Brother DCP-L2540DW",
    }


# --------------------------------------------------------------------------- #
# POST /printers/{id}/test-page
# --------------------------------------------------------------------------- #
async def test_test_page_success_returns_serialized_job(db, admin_client, monkeypatch):
    printer = Printer(
        display_name="Brother", cups_name="brother", uri="ipp://x/ipp", is_network_queue=False
    )
    db.add(printer)
    await db.commit()
    await db.refresh(printer)

    # Built in-memory (never added/committed) as a stand-in for print_test_page's
    # return value -- so fields that SQLAlchemy only fills in at INSERT time
    # (id, copies, duplex, media) need explicit values here, or serialization
    # via PrintJobResponse fails validation on the None defaults.
    job = PrintJob(
        id=1,
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
        printer_id=printer.id,
        cups_job_id=999,
        created_at=datetime(2026, 7, 5, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 5, tzinfo=timezone.utc),
    )

    async def fake_print_test_page(_db, _printer, _user):
        return job

    monkeypatch.setattr(printers_router, "print_test_page", fake_print_test_page)

    resp = await admin_client.post(f"/api/printers/{printer.id}/test-page")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "printing"
    assert body["cups_job_id"] == 999
    assert body["source_type"] == "test_page"


async def test_test_page_failure_is_502_with_request_id(db, admin_client, monkeypatch):
    printer = Printer(
        display_name="Brother", cups_name="brother", uri="ipp://x/ipp", is_network_queue=False
    )
    db.add(printer)
    await db.commit()
    await db.refresh(printer)

    async def fake_print_test_page(_db, _printer, _user):
        raise TestPageError("printer offline")

    monkeypatch.setattr(printers_router, "print_test_page", fake_print_test_page)

    resp = await admin_client.post(f"/api/printers/{printer.id}/test-page")
    assert resp.status_code == 502
    body = resp.json()
    assert body["detail"] == "printer offline"
    assert body["request_id"]
    assert resp.headers["x-request-id"] == body["request_id"]


# --------------------------------------------------------------------------- #
# RBAC
# --------------------------------------------------------------------------- #
async def test_non_admin_gets_403_on_admin_routes(user_client):
    resp = await user_client.get("/api/printers/discover")
    assert resp.status_code == 403

    resp = await user_client.post(
        "/api/printers", json={"display_name": "X", "uri": "", "is_network_queue": False}
    )
    assert resp.status_code == 403

    resp = await user_client.get("/api/printers/probe", params={"ip": "1.2.3.4"})
    assert resp.status_code == 403
