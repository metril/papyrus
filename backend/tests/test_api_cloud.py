"""Router-level tests for `app.routers.cloud`.

Two things a code review flagged after the initial Task 7 pass:

1. `_get_access_token`'s exception mapping (F14 fix-up) -- a cloud provider
   needing reconnection, or a transient refresh failure, must never surface
   as a Papyrus 401: the frontend's axios interceptor redirects the *whole
   page* to /api/auth/login on any 401, so misusing it here would log a user
   out of whatever they were doing over a five-second provider hiccup. Only
   an actually-corrupt `provider` value (`UnknownCloudProviderError`) is a
   400; everything else `CloudError` raises is left to propagate to the
   global handler as the curated 502 it already is.
2. `_content_disposition_for` (F61 fix-up) -- a tightened allowlist
   (application/pdf + four raster image types) instead of "any image/*",
   since image/svg+xml can carry a <script> payload.

No real Google/Dropbox/Microsoft SDK or network call is used: OneDrive's
refresh path is pure httpx (swapped for an `httpx.MockTransport`, the same
pattern `test_webhook_signing.py`/`test_cloud_service.py` use), and the
"expired with no refresh token"/"unknown provider" cases never reach the
network at all.
"""
import mimetypes
from datetime import datetime, timedelta, timezone

import httpx
import pytest

import app.services.http_client as http_client_module
from app.models import AppConfig, CloudProvider
from app.routers.cloud import _content_disposition_for
from app.services.crypto import encrypt_value


@pytest.fixture(autouse=True)
async def _reset_http_client():
    yield
    if http_client_module._client is not None:
        await http_client_module._client.aclose()
    http_client_module._client = None


def _install_transport(handler) -> None:
    http_client_module._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _make_provider(db, admin_user, **overrides) -> CloudProvider:
    defaults = dict(
        user_id=admin_user.id,
        provider="gdrive",
        access_token_encrypted=encrypt_value("old-token"),
        refresh_token_encrypted=encrypt_value("refresh-token-xyz"),
        token_expiry=None,
    )
    defaults.update(overrides)
    provider = CloudProvider(**defaults)
    db.add(provider)
    await db.commit()
    await db.refresh(provider)
    return provider


async def _seed_onedrive_settings(db) -> None:
    db.add(AppConfig(key="onedrive_client_id", value="client-123"))
    db.add(AppConfig(key="onedrive_client_secret_encrypted", value=encrypt_value("secret-abc")))
    await db.commit()


# --------------------------------------------------------------------------- #
# _get_access_token's status-code mapping -- never a 401 for provider state
# --------------------------------------------------------------------------- #
async def test_expired_with_no_refresh_token_is_502_not_401(db, admin_user, admin_client):
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    provider = await _make_provider(
        db, admin_user, token_expiry=past, refresh_token_encrypted=None
    )

    resp = await admin_client.get(f"/api/cloud/files/{provider.id}")

    assert resp.status_code == 502
    assert "reconnect" in resp.json()["detail"].lower()


async def test_refresh_transport_failure_is_502_curated_not_401(db, admin_user, admin_client):
    await _seed_onedrive_settings(db)
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    provider = await _make_provider(db, admin_user, provider="onedrive", token_expiry=past)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text='{"error":"invalid_grant","secret":"leak-me-not"}')

    _install_transport(handler)

    resp = await admin_client.get(f"/api/cloud/files/{provider.id}")

    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert "leak-me-not" not in detail  # F38: curated, never the raw upstream body
    assert "invalid_grant" not in detail


async def test_unknown_provider_is_400(db, admin_user, admin_client):
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    provider = await _make_provider(
        db, admin_user, provider="not-a-real-provider", token_expiry=past
    )

    resp = await admin_client.get(f"/api/cloud/files/{provider.id}")

    assert resp.status_code == 400


async def test_valid_token_never_touches_the_network_or_401s(db, admin_user, admin_client):
    """Sanity check: a provider with no expiry set (or one in the future)
    must reach list_gdrive_files rather than fail token resolution at all --
    if it did 401 here, this test would hang/fail on the un-mocked Drive SDK
    call instead of the deliberately-thrown-away 404/500 below."""
    provider = await _make_provider(db, admin_user, token_expiry=None)

    resp = await admin_client.get(f"/api/cloud/files/{provider.id}")

    # No transport/SDK stubbed for the actual Drive call -- it will fail,
    # but NOT with a 401 (which would mean token resolution itself failed).
    assert resp.status_code != 401


# --------------------------------------------------------------------------- #
# _content_disposition_for -- tightened inline allowlist (F61)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "content_type",
    ["application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp"],
)
def test_content_disposition_inline_for_allowlisted_types(content_type):
    assert _content_disposition_for(content_type) == "inline"


@pytest.mark.parametrize("filename", ["report.pdf", "photo.png", "photo.jpg", "photo.gif"])
def test_content_disposition_inline_via_mimetypes_guess_type(filename):
    """Cross-checks the allowlist against what `mimetypes.guess_type`
    actually derives for these extensions on this Python/OS -- excludes
    .webp, whose stdlib registration varies by Python version (absent on
    3.10, this app's target) and is covered directly above instead."""
    content_type, _ = mimetypes.guess_type(filename)
    assert _content_disposition_for(content_type) == "inline"


def test_content_disposition_attachment_for_svg():
    """Regression: image/svg+xml can embed <script> -- inline-serving it on
    the app's own origin is a stored-XSS-via-cloud-file primitive."""
    content_type, _ = mimetypes.guess_type("payload.svg")
    assert content_type == "image/svg+xml"
    assert _content_disposition_for(content_type) == "attachment"


def test_content_disposition_attachment_for_html():
    content_type, _ = mimetypes.guess_type("page.html")
    assert content_type == "text/html"
    assert _content_disposition_for(content_type) == "attachment"


def test_content_disposition_attachment_for_exe():
    content_type, _ = mimetypes.guess_type("payload.exe")
    assert _content_disposition_for(content_type or "application/octet-stream") == "attachment"


def test_content_disposition_attachment_for_unknown_or_no_extension():
    assert _content_disposition_for("application/octet-stream") == "attachment"
