"""Kiosk identities: CRUD, token handling, last-seen tracking."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.auth import dependencies as deps
from app.auth.tokens import hash_token
from app.main import app
from app.models import APIToken, Kiosk, User

K = "/api/v1/kiosks"


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


async def _mk(admin_client, name="Front"):
    r = await admin_client.post(K, json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


async def test_create_returns_token_once_and_service_user(admin_client, db):
    body = await _mk(admin_client)
    assert body["token"].startswith("pprs_")
    assert "token" not in body["kiosk"]
    kid = body["kiosk"]["id"]
    assert body["token"] not in (await admin_client.get(f"{K}/{kid}")).text
    assert body["token"] not in (await admin_client.get(K)).text
    svc = (await db.execute(select(User).where(User.username == "kiosk-front"))).scalar_one()
    assert svc.role == "user" and svc.is_local and svc.password_hash is None
    tok = (await db.execute(select(APIToken))).scalar_one()
    assert tok.user_id == svc.id and tok.expires_at is None
    assert tok.permissions == ["print", "scan", "device"]
    assert tok.token_hash == hash_token(body["token"])
    assert (await admin_client.post(K, json={"name": "Front"})).status_code == 409


async def test_list_get_rename(admin_client):
    kid = (await _mk(admin_client))["kiosk"]["id"]
    lst = (await admin_client.get(K)).json()
    assert lst[0]["id"] == kid and lst[0]["last_seen_at"] is None and lst[0]["token_id"]
    r = await admin_client.put(f"{K}/{kid}", json={"name": "Lobby"})
    assert r.json()["name"] == "Lobby"
    await _mk(admin_client, "Other")
    assert (await admin_client.put(f"{K}/{kid}", json={"name": "Other"})).status_code == 409
    assert (await admin_client.get(f"{K}/00000000-0000-0000-0000-000000000001")).status_code == 404


async def test_non_admin_forbidden(user_client):
    assert (await user_client.get(K)).status_code == 403
    assert (await user_client.post(K, json={"name": "x"})).status_code == 403


async def test_kiosk_token_is_not_admin(admin_client, client):
    body = await _mk(admin_client)
    app.dependency_overrides.clear()
    assert (await client.get(K, headers=_h(body["token"]))).status_code == 403


async def test_rotate_token(admin_client, client, db):
    body = await _mk(admin_client)
    kid = body["kiosk"]["id"]
    r = await admin_client.post(f"{K}/{kid}/rotate-token")
    assert r.status_code == 200
    new = r.json()["token"]
    assert new != body["token"]
    app.dependency_overrides.clear()
    assert (await client.get("/api/v1/contacts", headers=_h(body["token"]))).status_code == 401
    assert (await client.get("/api/v1/contacts", headers=_h(new))).status_code == 200
    tok = (await db.execute(select(APIToken))).scalar_one()
    assert tok.permissions == ["print", "scan", "device"]


async def test_delete_revokes_and_removes_user(admin_client, client, db):
    body = await _mk(admin_client)
    kid = body["kiosk"]["id"]
    assert (await admin_client.delete(f"{K}/{kid}")).status_code == 204
    app.dependency_overrides.clear()
    assert (await client.get("/api/v1/contacts", headers=_h(body["token"]))).status_code == 401
    assert (await db.execute(select(Kiosk))).first() is None
    assert (await db.execute(select(User).where(User.username == "kiosk-front"))).first() is None
    assert (await db.execute(select(APIToken))).first() is None


async def test_last_seen_updates_and_is_throttled(admin_client, client, db):
    body = await _mk(admin_client)
    kid = body["kiosk"]["id"]
    app.dependency_overrides.clear()
    h = _h(body["token"])
    k = (await db.execute(select(Kiosk).where(Kiosk.id == kid))).scalar_one()
    assert k.last_seen_at is None
    assert (await client.get("/api/v1/contacts", headers=h)).status_code == 200
    await db.refresh(k)
    first = k.last_seen_at
    assert first is not None
    # within the window: no write, even if the stored value is stale
    old = datetime.now(timezone.utc) - timedelta(hours=1)
    k.last_seen_at = old
    await db.commit()
    await client.get("/api/v1/contacts", headers=h)
    await db.refresh(k)
    assert k.last_seen_at == old
    # window elapsed: written again
    deps._seen_checked.clear()
    await client.get("/api/v1/contacts", headers=h)
    await db.refresh(k)
    assert k.last_seen_at > old


async def test_last_seen_ignores_other_tokens(client, db, admin_user):
    db.add(APIToken(user_id=admin_user.id, name="p", token_hash=hash_token("pprs_p"),
                    permissions=["print"]))
    await db.commit()
    assert (await client.get("/api/v1/contacts", headers=_h("pprs_p"))).status_code == 200
