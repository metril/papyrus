"""API settings suite — masking, unknown-key rejection, cache invalidation, RBAC.

Proves the write path invalidates the in-process settings_cache (settings.py
~L225): a value primed into the cache is observed as the *new* value
immediately after a PUT, not the stale cached one.
"""
from app.models import AppConfig
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
