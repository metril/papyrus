"""Device authorization grant (RFC 8628-style) end to end."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest_asyncio
from sqlalchemy import select

from app.auth.dependencies import get_current_user
from app.auth.tokens import hash_token
from app.main import app
from app.models import APIToken, CloudProvider, DeviceAuthorization, ScanJob
from app.routers import scanner as scanner_router

BASE = "/api/v1/auth/device"
KIOSK = "pprs_kiosk_shared_token"


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


async def _token(db, user, plaintext, perms):
    t = APIToken(user_id=user.id, name="t", token_hash=hash_token(plaintext), permissions=perms)
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return t


@pytest_asyncio.fixture(loop_scope="function")
async def kiosk(db, admin_user):
    return await _token(db, admin_user, KIOSK, ["device"])


async def _start(client, name="Lobby"):
    r = await client.post(f"{BASE}/code", json={"client_name": name}, headers=_h(KIOSK))
    assert r.status_code == 200, r.text
    return r.json()


async def _approve(client, user, code, approve=True):
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        return await client.post(f"{BASE}/approve", json={"user_code": code, "approve": approve})
    finally:
        app.dependency_overrides.clear()


async def _poll(client, device_code, tok=KIOSK):
    return await client.post(f"{BASE}/token", json={"device_code": device_code}, headers=_h(tok))


async def _unthrottle(db):
    await db.execute(DeviceAuthorization.__table__.update().values(last_polled_at=None))
    await db.commit()


async def test_code_shape_and_hash_storage(db, client, kiosk):
    body = await _start(client)
    assert len(body["user_code"]) == 9 and body["user_code"][4] == "-"
    assert not set(body["user_code"]) & set("01OIL")
    assert body["verification_uri_complete"].endswith(f"/device?code={body['user_code']}")
    assert body["interval"] == 5 and body["expires_in"] == 300
    row = (await db.execute(select(DeviceAuthorization))).scalar_one()
    assert row.device_code_hash == hash_token(body["device_code"])
    assert row.device_code_hash != body["device_code"]


async def test_code_requires_device_permission_and_token(db, client, kiosk, admin_user):
    await _token(db, admin_user, "pprs_noperm", ["print"])
    r = await client.post(f"{BASE}/code", headers=_h("pprs_noperm"))
    assert r.status_code == 403
    app.dependency_overrides[get_current_user] = lambda: admin_user
    r = await client.post(f"{BASE}/code")
    assert r.status_code == 403  # session, not a token


async def test_pending_slow_down_approve_mint(db, client, kiosk, regular_user):
    c = await _start(client)
    r = await _poll(client, c["device_code"])
    assert (r.status_code, r.json()["code"]) == (400, "authorization_pending")
    r = await _poll(client, c["device_code"])
    assert r.json()["code"] == "slow_down"
    row = (await db.execute(select(DeviceAuthorization))).scalar_one()
    await db.refresh(row)
    assert row.interval == 10

    # lookup (session)
    app.dependency_overrides[get_current_user] = lambda: regular_user
    r = await client.get(f"{BASE}/{c['user_code'].replace('-', '').lower()}")
    app.dependency_overrides.clear()
    assert r.status_code == 200
    assert r.json()["client_name"] == "Lobby" and r.json()["status"] == "pending"
    assert r.json()["scopes"] == ["print", "scan", "files", "email"]

    assert (await _approve(client, regular_user, c["user_code"])).status_code == 200
    assert (await _approve(client, regular_user, c["user_code"])).status_code == 409
    await _unthrottle(db)
    r = await _poll(client, c["device_code"])
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["scopes"] == ["print", "scan", "files", "email"]
    assert b["user"]["id"] == str(regular_user.id) and b["user"]["email"] == regular_user.email
    exp = datetime.fromisoformat(b["expires_at"])
    assert timedelta(minutes=14) < exp - datetime.now(timezone.utc) <= timedelta(minutes=15)
    tok = (await db.execute(select(APIToken).where(APIToken.id == b["token_id"]))).scalar_one()
    assert tok.user_id == regular_user.id and tok.name == "Device: Lobby"
    assert sorted(tok.permissions) == ["email", "files", "print", "scan"]
    # minted token works
    me = await client.get("/api/v1/auth/me", headers=_h(b["access_token"]))
    assert me.json()["id"] == str(regular_user.id)

    # single use
    await _unthrottle(db)
    r = await _poll(client, c["device_code"])
    assert r.json()["code"] == "invalid_grant"


async def test_deny(db, client, kiosk, regular_user):
    c = await _start(client)
    assert (await _approve(client, regular_user, c["user_code"], approve=False)).status_code == 200
    r = await _poll(client, c["device_code"])
    assert r.json()["code"] == "access_denied"


async def test_expiry(db, client, kiosk, regular_user):
    c = await _start(client)
    await db.execute(DeviceAuthorization.__table__.update().values(
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
    await db.commit()
    assert (await _poll(client, c["device_code"])).json()["code"] == "expired_token"
    assert (await _approve(client, regular_user, c["user_code"])).status_code == 404


async def test_wrong_token_and_unknown_code(db, client, kiosk, admin_user):
    c = await _start(client)
    await _token(db, admin_user, "pprs_other_kiosk", ["device"])
    r = await _poll(client, c["device_code"], tok="pprs_other_kiosk")
    assert r.json()["code"] == "invalid_grant"
    assert (await _poll(client, "nope")).json()["code"] == "invalid_grant"


async def test_token_requires_device_permission(db, client, kiosk, admin_user):
    await _token(db, admin_user, "pprs_noperm", ["print"])
    assert (await _poll(client, "x", tok="pprs_noperm")).status_code == 403


async def test_approve_rejects_api_tokens(db, client, kiosk, admin_user):
    c = await _start(client)
    await _token(db, admin_user, "pprs_adm", ["admin", "device"])
    r = await client.post(f"{BASE}/approve", json={"user_code": c["user_code"], "approve": True},
                          headers=_h("pprs_adm"))
    assert r.status_code == 403
    r = await client.get(f"{BASE}/{c['user_code']}", headers=_h("pprs_adm"))
    assert r.status_code == 403


async def test_concurrent_polls_mint_once(db, client, kiosk, regular_user):
    c = await _start(client)
    await _approve(client, regular_user, c["user_code"])
    await _unthrottle(db)
    # Same-second polls would slow_down; clear the throttle stamp via interval 0.
    await db.execute(DeviceAuthorization.__table__.update().values(interval=0))
    await db.commit()
    rs = await asyncio.gather(*[_poll(client, c["device_code"]) for _ in range(5)])
    assert sorted(r.status_code for r in rs).count(200) == 1
    q = select(APIToken).where(APIToken.name == "Device: Lobby")
    minted = (await db.execute(q)).scalars().all()
    assert len(minted) == 1


async def test_rate_limits(db, client, kiosk, regular_user):
    codes = [(await client.post(f"{BASE}/code", headers=_h(KIOSK))).status_code for _ in range(11)]
    assert codes[:10] == [200] * 10 and codes[10] == 429
    r = await client.post(f"{BASE}/code", headers=_h(KIOSK))
    assert r.json()["code"] == "rate_limited" and int(r.headers["retry-after"]) >= 1
    app.dependency_overrides[get_current_user] = lambda: regular_user
    rs = [(await client.get(f"{BASE}/ZZZZ-ZZZZ")).status_code for _ in range(21)]
    assert rs[:20] == [404] * 20 and rs[20] == 429


async def test_revocation_paths(db, client, kiosk, regular_user, admin_user):
    c = await _start(client)
    await _approve(client, regular_user, c["user_code"])
    await _unthrottle(db)
    b = (await _poll(client, c["device_code"])).json()
    # self-revoke
    r = await client.delete(f"/api/v1/auth/tokens/{b['token_id']}", headers=_h(b["access_token"]))
    assert r.status_code == 204
    # device token revokes its own mint
    c = await _start(client)
    await _approve(client, regular_user, c["user_code"])
    await _unthrottle(db)
    b = (await _poll(client, c["device_code"])).json()
    r = await client.delete(f"/api/v1/auth/tokens/{b['token_id']}", headers=_h(KIOSK))
    assert r.status_code == 204
    assert (await client.get("/api/v1/auth/me", headers=_h(b["access_token"]))).status_code == 401
    # but not arbitrary tokens
    other = await _token(db, regular_user, "pprs_victim", ["print"])
    r = await client.delete(f"/api/v1/auth/tokens/{other.id}", headers=_h(KIOSK))
    assert r.status_code == 403
    # nor another token deleting someone else's
    r = await client.delete(f"/api/v1/auth/tokens/{other.id}", headers=_h("pprs_victim"))
    assert r.status_code == 204


async def test_scans_mine_filter(db, client, admin_user, regular_user):
    db.add_all([
        ScanJob(user_id=regular_user.id, status="completed"),
        ScanJob(user_id=admin_user.id, status="completed"),
        ScanJob(user_id=None, status="completed"),
    ])
    await db.commit()
    app.dependency_overrides[get_current_user] = lambda: regular_user
    allr = (await client.get("/api/v1/scans")).json()
    mine = (await client.get("/api/v1/scans?mine=true")).json()
    assert allr["total"] == 2  # own + ownerless network scan, not the admin's
    assert mine["total"] == 1 and mine["items"][0]["id"] is not None


async def test_cloud_folder_passthrough(db, client, regular_user, monkeypatch):
    prov = CloudProvider(user_id=regular_user.id, provider="gdrive", access_token_encrypted="x")
    db.add(prov)
    scan = ScanJob(user_id=regular_user.id, status="completed", filepath="/tmp/x.pdf", format="pdf")
    db.add(scan)
    await db.commit()
    await db.refresh(prov)
    seen = {}

    async def fake_token(*a, **k):
        return "tok"

    async def fake_upload(**kw):
        seen.update(kw)
        return "fid"

    monkeypatch.setattr(scanner_router.cloud_service, "get_valid_access_token", fake_token)
    monkeypatch.setattr(scanner_router.cloud_service, "upload_to_gdrive", fake_upload)
    app.dependency_overrides[get_current_user] = lambda: regular_user
    url = f"/api/v1/scans/{scan.scan_id}/cloud?provider_id={prov.id}&folder_id=abc123"
    r = await client.post(url)
    assert r.status_code == 200, r.text
    assert seen["folder_id"] == "abc123"
