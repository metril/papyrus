"""Webhook/WebSocket message models: coverage and conformance with real payloads."""
import json

import httpx
import pytest
from pydantic import TypeAdapter

import app.services.http_client as http_client_module
from app.main import app
from app.models import Webhook
from app.schemas import serialize_print_job, serialize_scan_job
from app.schemas.events import (
    WEBHOOK_MODELS,
    WS_MESSAGE_MODELS,
    JobsMessage,
    PrinterStatusMessage,
    ScansMessage,
)
from app.services.webhook_service import (
    WEBHOOK_EVENTS,
    dispatch_webhook,
    wait_for_pending_dispatches,
)
from tests.test_ws_serialization import _make_print_job, _make_scan_job  # noqa: F401


def test_every_webhook_event_has_model_and_schema_entry():
    schema = app.openapi()
    assert set(WEBHOOK_MODELS) == set(WEBHOOK_EVENTS)
    assert set(schema["webhooks"]) == set(WEBHOOK_EVENTS)
    for event in WEBHOOK_EVENTS:
        assert schema["webhooks"][event]["post"]["requestBody"]


def test_ws_models_in_components():
    comps = app.openapi()["components"]["schemas"]
    for models in WS_MESSAGE_MODELS.values():
        for m in models:
            assert m.__name__ in comps


async def _capture(db, admin_user, event, data):
    db.add(Webhook(name="h", url="http://example.test/h", secret="s", events=[event],
                   enabled=True, created_by=admin_user.id))
    await db.commit()
    got = {}

    def handler(request: httpx.Request) -> httpx.Response:
        got["body"] = json.loads(request.content)
        return httpx.Response(200)

    http_client_module._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await dispatch_webhook(db, event, data)
    await wait_for_pending_dispatches()
    return got["body"]


@pytest.mark.parametrize("event,data", [
    ("print.release", {"id": 1, "title": "t", "copies": 2}),
    ("print.upload", {"id": 1, "title": "t", "user_id": None, "source_type": "upload"}),
    ("print.held", {"id": 1, "title": "t", "username": "bob", "source_type": "network"}),
    ("scan.complete", {"scan_id": "abc", "format": "pdf", "pages": 3}),
    ("settings.update", {"keys": ["ocr_enabled"]}),
])
async def test_dispatched_payload_validates(db, admin_user, event, data):
    body = await _capture(db, admin_user, event, data)
    WEBHOOK_MODELS[event].model_validate(body)


async def test_alert_payload_validates(db, admin_user):
    from types import SimpleNamespace

    from app.services import alert_service

    printer = SimpleNamespace(id=1, display_name="P", cups_name="p")
    cond = {"event": "printer.supply_low", "message": "Low supply: Black at 5%",
            "data": {"markers": [{"name": "Black", "level": 5}]}}
    db.add(Webhook(name="h", url="http://example.test/h", secret=None,
                   events=["printer.supply_low"], enabled=True, created_by=admin_user.id))
    await db.commit()
    got = {}

    def handler(request: httpx.Request) -> httpx.Response:
        got["body"] = json.loads(request.content)
        return httpx.Response(200)

    http_client_module._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await alert_service._dispatch(db, printer, cond, resolved=False, alert_email="")
    await wait_for_pending_dispatches()
    WEBHOOK_MODELS["printer.supply_low"].model_validate(got["body"])


def test_ws_payloads_validate():
    job = serialize_print_job(_make_print_job())
    scan = serialize_scan_job(_make_scan_job())
    jobs = TypeAdapter(JobsMessage)
    jobs.validate_python({"type": "job_created", "data": job})
    jobs.validate_python({"type": "job_updated", "data": job})
    jobs.validate_python({"type": "job_deleted", "data": {"id": 3}})
    scans = TypeAdapter(ScansMessage)
    scans.validate_python({"type": "scan_completed", "data": scan})
    scans.validate_python({"type": "scan_deleted", "data": {"scan_id": "x"}})
    PrinterStatusMessage.model_validate({"type": "printer_status", "data": {
        "id": 1, "cups_name": "p", "state": 3, "state_message": "", "accepting_jobs": True,
        "markers": [], "state_reasons": []}})


async def test_real_broadcasts_validate(admin_client, db, monkeypatch):
    from app.main import _broadcast_changed_printer_statuses
    from app.services.ws_manager import ws_manager

    sent: list[tuple[str, dict]] = []

    async def fake(channel, message):
        sent.append((channel, message))

    monkeypatch.setattr(ws_manager, "broadcast", fake)
    status = {"id": 1, "cups_name": "p", "state": 3, "state_message": "",
              "accepting_jobs": True, "markers": [], "state_reasons": []}
    await _broadcast_changed_printer_statuses({"p": status}, {})

    job = _make_print_job()
    job.id = None
    job.release_pin = None
    job.printer_id = None
    job.filepath = "/nonexistent/x.pdf"
    db.add(job)
    await db.commit()
    resp = await admin_client.delete(f"/api/v1/jobs/{job.id}")
    assert resp.status_code in (200, 204)

    seen = {m["type"] for _, m in sent}
    assert {"printer_status", "job_deleted"} <= seen
    adapters = {
        "printers": TypeAdapter(PrinterStatusMessage),
        "jobs": TypeAdapter(JobsMessage),
    }
    for channel, msg in sent:
        adapters[channel].validate_python(msg)
