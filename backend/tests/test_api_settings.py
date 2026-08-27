"""API settings suite — masking, unknown-key rejection, cache invalidation, RBAC.

Proves the write path invalidates the in-process settings_cache (settings.py
~L225): a value primed into the cache is observed as the *new* value
immediately after a PUT, not the stale cached one.
"""
from app.models import AppConfig
from app.routers import settings as settings_router
from app.routers.settings import get_setting
from app.services.crypto import encrypt_value


async def test_get_settings_masks_encrypted_and_merges_defaults(db, admin_client):
    db.add(AppConfig(key="smtp_password_encrypted", value=encrypt_value("hunter2")))
    await db.commit()

    resp = await admin_client.get("/api/settings")
    assert resp.status_code == 200
    body = resp.json()
    # Encrypted secret present in DB -> masked, never returned in the clear.
    assert body["smtp_password"] == "*set*"
    assert "hunter2" not in resp.text
    # Unset key falls back to its default in the merged dict.
    assert body["ocr_language"] == "eng"


async def test_put_unknown_key_is_400(admin_client):
    resp = await admin_client.put("/api/settings", json={"definitely_not_a_setting": "x"})
    assert resp.status_code == 400


async def test_put_invalidates_settings_cache(db, admin_client):
    # Prime the cache with the current (unset -> None) value.
    assert await get_setting(db, "ocr_language") is None

    resp = await admin_client.put("/api/settings", json={"ocr_language": "deu"})
    assert resp.status_code == 200

    # If the write path had not invalidated the cache, this would still read the
    # primed None. It reads the freshly written value instead.
    await db.rollback()
    assert await get_setting(db, "ocr_language") == "deu"


async def test_put_settings_as_non_admin_is_403(user_client):
    resp = await user_client.put("/api/settings", json={"ocr_language": "deu"})
    assert resp.status_code == 403


async def test_put_accepts_new_alert_settings(db, admin_client):
    """The alert keys registered in CONFIGURABLE/DEFAULTS round-trip through PUT
    (a 400 here would mean they weren't registered and the poller couldn't be
    configured from the UI)."""
    resp = await admin_client.put("/api/settings", json={
        "alerts_enabled": True,
        "alert_toner_threshold": 15,
        "alert_email": "ops@example.com",
        "alert_poll_minutes": 10,
    })
    assert resp.status_code == 200

    body = (await admin_client.get("/api/settings")).json()
    assert body["alerts_enabled"] is True
    assert body["alert_toner_threshold"] == 15
    assert body["alert_email"] == "ops@example.com"
    assert body["alert_poll_minutes"] == 10


async def test_put_settings_dispatches_settings_update_webhook(db, admin_client, monkeypatch):
    """Regression (F137): settings.update was listed in WEBHOOK_EVENTS and
    offered by GET /api/webhooks/events, but dispatch_webhook was never
    called with it — a subscriber saved as enabled and never received a
    request."""
    events: list = []

    async def fake_dispatch(_db, event, data):
        events.append((event, data))

    monkeypatch.setattr(settings_router, "dispatch_webhook", fake_dispatch)

    resp = await admin_client.put("/api/settings", json={"ocr_language": "deu"})
    assert resp.status_code == 200

    assert events == [("settings.update", {"keys": ["ocr_language"]})]


async def test_put_settings_no_changed_keys_does_not_dispatch_webhook(
    db, admin_client, monkeypatch
):
    """An all-placeholder PUT (no real change) must not fire a spurious
    settings.update — mirrors the existing changed_keys/log_event guard."""
    db.add(AppConfig(key="smtp_password_encrypted", value=encrypt_value("hunter2")))
    await db.commit()

    events: list = []

    async def fake_dispatch(_db, event, data):
        events.append((event, data))

    monkeypatch.setattr(settings_router, "dispatch_webhook", fake_dispatch)

    resp = await admin_client.put("/api/settings", json={"smtp_password": "*set*"})
    assert resp.status_code == 200
    assert events == []


# --------------------------------------------------------------------------- #
# F64 — int/bool settings are validated and coerced on write, not stored raw
# --------------------------------------------------------------------------- #
async def test_put_garbage_int_setting_is_400_with_field_name(db, admin_client):
    """Regression (F64): {"smtp_port": "587 (TLS)"} used to be stored
    verbatim as a string and only blow up much later at a bare int() deep in
    email_service. It must be rejected up front, naming the offending key."""
    resp = await admin_client.put("/api/settings", json={"smtp_port": "587 (TLS)"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Setting smtp_port must be an integer"

    # Nothing was persisted -- a later GET still falls back to the default.
    await db.rollback()
    assert await get_setting(db, "smtp_port") is None


async def test_put_numeric_string_int_setting_is_coerced_and_stored(db, admin_client):
    resp = await admin_client.put("/api/settings", json={"smtp_port": "465"})
    assert resp.status_code == 200

    await db.rollback()
    assert await get_setting(db, "smtp_port") == "465"


async def test_put_garbage_bool_setting_is_400_with_field_name(admin_client):
    resp = await admin_client.put("/api/settings", json={"escl_enabled": "not-a-boolean"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Setting escl_enabled must be a boolean"


async def test_put_string_bool_setting_is_coerced_and_stored(db, admin_client):
    """The frontend always PUTs settings as strings (SettingsPage.tsx stringifies
    every field, including checkboxes), so "true"/"false" must round-trip."""
    resp = await admin_client.put("/api/settings", json={"escl_enabled": "false"})
    assert resp.status_code == 200

    await db.rollback()
    assert await get_setting(db, "escl_enabled") == "false"


# --------------------------------------------------------------------------- #
# F131 — dev_mode is env-only infrastructure, not a DB-backed setting
# --------------------------------------------------------------------------- #
async def test_dev_mode_is_not_exposed_or_configurable(admin_client):
    """Regression (F131): dev_mode used to render as an editable Settings-UI
    checkbox that persisted to the DB but nothing ever read back -- flipping
    it did nothing, silently. It's an env-only infrastructure setting now."""
    resp = await admin_client.get("/api/settings")
    assert resp.status_code == 200
    assert "dev_mode" not in resp.json()

    put_resp = await admin_client.put("/api/settings", json={"dev_mode": True})
    assert put_resp.status_code == 400
