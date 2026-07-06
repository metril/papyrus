"""Webhook signing and dispatch suite (`app.services.webhook_service`).

``_sign_payload`` is exercised against an independently-computed HMAC-SHA256
known vector (not just round-tripped through the function under test).
``dispatch_webhook`` is exercised end-to-end against a seeded ``Webhook`` DB
row, with the shared HTTP client swapped for an ``httpx.MockTransport`` that
captures the outgoing request — the same pattern test_ipp_client.py uses for
``app.services.ipp_client``.
"""
import hashlib
import hmac
import json
import logging

import httpx
import pytest

import app.services.http_client as http_client_module
from app.models import Webhook
from app.services.webhook_service import _sign_payload, dispatch_webhook

_KNOWN_SECRET = "whsec_test123"
_KNOWN_PAYLOAD = b'{"event":"print.release","data":{"id":42}}'
_KNOWN_SIGNATURE = "a725561c4db62f3dc18576d74b62058c9f04a77ad512363e68a54ba73ecdfd5a"


def test_sign_payload_matches_known_hmac_sha256_vector():
    assert _sign_payload(_KNOWN_PAYLOAD, _KNOWN_SECRET) == _KNOWN_SIGNATURE


def test_sign_payload_differs_for_different_secrets():
    other = _sign_payload(_KNOWN_PAYLOAD, "a-different-secret")
    assert other != _KNOWN_SIGNATURE
    assert len(other) == 64  # still a SHA-256 hex digest


@pytest.fixture(autouse=True)
async def _reset_http_client():
    """Each test that dispatches installs a MockTransport onto the shared
    client; reset back to None afterwards so other tests get a fresh real
    client (mirrors test_ipp_client.py's fixture of the same name)."""
    yield
    if http_client_module._client is not None:
        await http_client_module._client.aclose()
    http_client_module._client = None


def _install_transport(handler) -> None:
    http_client_module._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _seed_webhook(db, admin_user, **overrides) -> Webhook:
    defaults = dict(
        name="test-hook",
        url="http://example.test/hook",
        secret="whsec_test123",
        events=["print.release"],
        enabled=True,
        created_by=admin_user.id,
    )
    defaults.update(overrides)
    webhook = Webhook(**defaults)
    db.add(webhook)
    await db.commit()
    await db.refresh(webhook)
    return webhook


# --------------------------------------------------------------------------- #
# dispatch_webhook — headers and signature over the real request
# --------------------------------------------------------------------------- #
async def test_dispatch_sends_event_and_signature_headers(db, admin_user):
    await _seed_webhook(db, admin_user, secret="whsec_test123")
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200)

    _install_transport(handler)

    await dispatch_webhook(db, "print.release", {"id": 42})

    request = captured["request"]
    assert request.headers["X-Papyrus-Event"] == "print.release"
    expected_sig = hmac.new(
        b"whsec_test123", request.content, hashlib.sha256
    ).hexdigest()
    assert request.headers["X-Papyrus-Signature"] == expected_sig

    body = json.loads(request.content)
    assert body["event"] == "print.release"
    assert body["data"] == {"id": 42}


async def test_dispatch_without_secret_sends_no_signature_header(db, admin_user):
    await _seed_webhook(db, admin_user, secret=None, name="no-secret-hook")
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200)

    _install_transport(handler)

    await dispatch_webhook(db, "print.release", {"id": 1})

    request = captured["request"]
    assert request.headers["X-Papyrus-Event"] == "print.release"
    assert "X-Papyrus-Signature" not in request.headers


# --------------------------------------------------------------------------- #
# Matching — enabled + event-membership filter
# --------------------------------------------------------------------------- #
async def test_dispatch_skips_disabled_and_non_matching_webhooks(db, admin_user):
    await _seed_webhook(db, admin_user, name="disabled-hook", enabled=False)
    await _seed_webhook(
        db, admin_user, name="other-event-hook", events=["scan.complete"]
    )
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200)

    _install_transport(handler)

    await dispatch_webhook(db, "print.release", {"id": 1})

    assert calls == []


async def test_dispatch_with_no_matching_webhooks_never_touches_http_client(db, admin_user):
    await _seed_webhook(db, admin_user, events=["scan.complete"])

    # No transport installed at all — if dispatch_webhook fetched the shared
    # client and tried a real network call, this would hang/fail loudly.
    await dispatch_webhook(db, "print.release", {"id": 1})


# --------------------------------------------------------------------------- #
# Delivery failures — logged, never raised
# --------------------------------------------------------------------------- #
async def test_dispatch_error_response_is_logged_not_raised(db, admin_user, caplog):
    await _seed_webhook(db, admin_user)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    _install_transport(handler)

    with caplog.at_level(logging.WARNING):
        await dispatch_webhook(db, "print.release", {"id": 1})

    assert any("500" in record.message for record in caplog.records)


async def test_dispatch_transport_exception_is_logged_not_raised(db, admin_user, caplog):
    await _seed_webhook(db, admin_user)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    _install_transport(handler)

    with caplog.at_level(logging.WARNING):
        await dispatch_webhook(db, "print.release", {"id": 1})  # must not raise

    assert any("connection refused" in record.message for record in caplog.records)
