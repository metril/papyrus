"""Tests for `cloud_service.get_valid_access_token` (F14).

This is the shared expiry-check/refresh helper extracted from
`routers/cloud.py`'s browse/download path so upload paths (scanner
save-to-cloud, scan auto-deliver) stop handing the provider SDK a possibly
hour-stale token. Only the OneDrive refresh path is exercised here (pure
httpx, no optional SDK) — gdrive/dropbox go through the same code shape but
need their respective SDKs installed, which this dev environment doesn't
have (matching every other cloud test gap noted in the audit).
"""
import logging
from datetime import datetime, timedelta, timezone

import httpx
import pytest

import app.services.http_client as http_client_module
from app.models import AppConfig, CloudProvider
from app.services.cloud_service import CloudError, cloud_service
from app.services.crypto import decrypt_value, encrypt_value


@pytest.fixture(autouse=True)
async def _reset_http_client():
    yield
    if http_client_module._client is not None:
        await http_client_module._client.aclose()
    http_client_module._client = None


def _install_transport(handler) -> None:
    http_client_module._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _seed_onedrive_settings(db) -> None:
    db.add(AppConfig(key="onedrive_client_id", value="client-123"))
    db.add(AppConfig(key="onedrive_client_secret_encrypted", value=encrypt_value("secret-abc")))
    await db.commit()


async def _make_provider(db, admin_user, **overrides) -> CloudProvider:
    defaults = dict(
        user_id=admin_user.id,
        provider="onedrive",
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


async def test_returns_decrypted_token_without_refresh_when_not_expired(db, admin_user):
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    provider = await _make_provider(db, admin_user, token_expiry=future)

    # No transport installed -- a refresh call here would hang/fail loudly.
    token = await cloud_service.get_valid_access_token(db, provider)

    assert token == "old-token"


async def test_returns_decrypted_token_when_no_expiry_set(db, admin_user):
    provider = await _make_provider(db, admin_user, token_expiry=None)

    token = await cloud_service.get_valid_access_token(db, provider)

    assert token == "old-token"


async def test_refreshes_and_persists_new_token_when_expired(db, admin_user):
    await _seed_onedrive_settings(db)
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    provider = await _make_provider(db, admin_user, token_expiry=past)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "new-token", "expires_in": 3600})

    _install_transport(handler)

    token = await cloud_service.get_valid_access_token(db, provider)

    assert token == "new-token"
    # Persisted back onto the row, not just returned.
    assert decrypt_value(provider.access_token_encrypted) == "new-token"
    assert provider.token_expiry > datetime.now(timezone.utc)


async def test_raises_curated_error_when_expired_with_no_refresh_token(db, admin_user):
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    provider = await _make_provider(
        db, admin_user, token_expiry=past, refresh_token_encrypted=None
    )

    with pytest.raises(CloudError, match="reconnect"):
        await cloud_service.get_valid_access_token(db, provider)


async def test_refresh_failure_never_leaks_upstream_body(db, admin_user, caplog):
    """F38: the raised CloudError must be a curated message, with the raw
    provider error body only reaching the log, never the client-visible
    detail."""
    await _seed_onedrive_settings(db)
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    provider = await _make_provider(db, admin_user, token_expiry=past)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text='{"error":"invalid_grant","secret_hint":"abc123"}')

    _install_transport(handler)

    with caplog.at_level(logging.WARNING):
        with pytest.raises(CloudError) as excinfo:
            await cloud_service.get_valid_access_token(db, provider)

    assert "secret_hint" not in str(excinfo.value)
    assert any("secret_hint" in r.message for r in caplog.records)
