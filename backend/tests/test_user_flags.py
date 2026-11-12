"""Per-user is_active / kiosk_enabled flags (migration 020)."""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.auth.dependencies import get_current_user
from app.auth.tokens import hash_token
from app.auth.ws import authenticate_websocket
from app.main import app
from app.models import APIToken, AuditEntry, DeviceAuthorization, Kiosk, User
from app.routers import auth as auth_router
from app.routers import device as device_router
from app.routers.auth import _ph
from app.services import kiosk_login as kl
from app.services import settings_cache
from app.services.ws_manager import ws_manager

A = "/api/v1/auth"
D = "/api/v1/auth/device"
ADM = "/api/v1/admin/users"
KIOSK = "pprs_flags_kiosk"


def _h(tok=KIOSK):
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    device_router._approve_request_limiter.clear()
    device_router._approve_limiter.clear()
    for k in ("kiosk_approve_enabled", "kiosk_pin_enabled"):
        settings_cache.put(k, "true")
    monkeypatch.setattr(ws_manager, "broadcast_user", AsyncMock())
    yield
    for k in ("kiosk_approve_enabled", "kiosk_pin_enabled"):
        settings_cache.invalidate(k)


@pytest_asyncio.fixture(loop_scope="function")
async def kiosk(db, admin_user):
    t = APIToken(user_id=admin_user.id, name="k", token_hash=hash_token(KIOSK),
                 permissions=["device"])
    db.add(t)
    await db.commit()
    return t


async def _flag(db, user, **kw):
    for k, v in kw.items():
        setattr(user, k, v)
    await db.commit()


async def _tok(db, user, name="t", perms=("print",)):
    plain = f"pprs_{uuid.uuid4().hex}"
    db.add(APIToken(user_id=user.id, name=name, token_hash=hash_token(plain),
                    permissions=list(perms)))
    await db.commit()
    return plain


async def _as(user, fn):
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        return await fn()
    finally:
        app.dependency_overrides.clear()


# ---- login / sessions / tokens / ws ----

async def test_local_login_refused_for_inactive(db, client):
    u = User(email="i@x", display_name="I", role="user", is_local=True, username="inact",
             password_hash=_ph.hash("pw-123456"))
    db.add(u)
    await db.commit()
    ok = await client.post(f"{A}/local-login", json={"username": "inact", "password": "pw-123456"})
    assert ok.status_code == 200
    await _flag(db, u, is_active=False)
    r = await client.post(f"{A}/local-login", json={"username": "inact", "password": "pw-123456"})
    assert r.status_code == 403 and "deactivated" in r.json()["detail"]
    bad = await client.post(f"{A}/local-login", json={"username": "inact", "password": "nope"})
    assert bad.status_code == 401  # wrong password never reveals the state


async def test_oidc_callback_refused_for_inactive(db, client, monkeypatch):
    u = User(email="o@x", display_name="O", role="user", oidc_sub="sub-1")
    db.add(u)
    await db.commit()
    fake = SimpleNamespace(authorize_access_token=AsyncMock(
        return_value={"userinfo": {"sub": "sub-1", "email": "o@x", "name": "O"}}))
    monkeypatch.setattr(auth_router.oauth, "papyrus", fake, raising=False)
    r = await client.get(f"{A}/callback", follow_redirects=False)
    assert r.status_code == 302
    await _flag(db, u, is_active=False)
    r = await client.get(f"{A}/callback", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/?login_error=deactivated"
    assert "session" not in r.headers.get("set-cookie", "")


async def test_inactive_user_token_and_session_401(db, client, regular_user):
    tok = await _tok(db, regular_user)
    assert (await client.get(f"{A}/me", headers=_h(tok))).status_code == 200
    await _flag(db, regular_user, is_active=False)
    r = await client.get(f"{A}/me", headers=_h(tok))
    assert r.status_code == 401
    # session cookie path
    from starlette.requests import Request

    from app.auth.dependencies import get_current_user as gcu
    scope = {"type": "http", "headers": [], "session": {"user_id": str(regular_user.id)}}
    with pytest.raises(Exception) as ei:
        await gcu(Request(scope), db)
    assert getattr(ei.value, "status_code", None) == 401
    await _flag(db, regular_user, is_active=True)
    assert (await client.get(f"{A}/me", headers=_h(tok))).status_code == 200


async def test_ws_rejects_inactive(db, regular_user):
    tok = await _tok(db, regular_user)
    ws = SimpleNamespace(headers={}, query_params={"token": tok}, session={})
    assert await authenticate_websocket(ws, db) is not None
    ws_s = SimpleNamespace(headers={}, query_params={}, session={"user_id": str(regular_user.id)})
    assert await authenticate_websocket(ws_s, db) is not None
    await _flag(db, regular_user, is_active=False)
    assert await authenticate_websocket(ws, db) is None
    assert await authenticate_websocket(ws_s, db) is None


async def test_kiosk_service_user_unaffected_by_other_flags(db, client, regular_user):
    svc = User(email="s@x", display_name="Svc", role="user", username="kiosk-lobby")
    db.add(svc)
    await db.commit()
    tok = await _tok(db, svc)
    db.add(Kiosk(name="lobby", token_id=(await db.execute(
        select(APIToken.id).where(APIToken.user_id == svc.id))).scalar_one(),
        service_user_id=svc.id))
    await _flag(db, regular_user, is_active=False)
    assert (await client.get(f"{A}/me", headers=_h(tok))).status_code == 200
    await _flag(db, svc, is_active=False)
    assert (await client.get(f"{A}/me", headers=_h(tok))).status_code == 401


# ---- admin API + guards ----

async def test_admin_list_and_toggle_with_audit(db, admin_client, admin_user, regular_user):
    lst = (await admin_client.get(ADM)).json()
    row = next(u for u in lst if u["id"] == str(regular_user.id))
    assert row["is_active"] is True and row["kiosk_enabled"] is True
    r = await admin_client.patch(f"{ADM}/{regular_user.id}",
                                 json={"is_active": False, "kiosk_enabled": False})
    assert r.status_code == 200
    assert r.json() == {"id": str(regular_user.id), "role": "user", "is_active": False,
                        "kiosk_enabled": False}
    r = await admin_client.patch(f"{ADM}/{regular_user.id}", json={"role": "admin"})
    assert r.json()["is_active"] is False  # omitted fields unchanged
    r = await admin_client.patch(f"{ADM}/{regular_user.id}", json={"is_active": True})
    assert r.json()["is_active"] is True
    acts = {a.action for a in (await db.execute(select(AuditEntry))).scalars()}
    assert {"user.deactivate", "user.kiosk_disable", "user.activate",
            "user.role_change"} <= acts


async def test_cannot_deactivate_self(db, admin_client, admin_user):
    r = await admin_client.patch(f"{ADM}/{admin_user.id}", json={"is_active": False})
    assert r.status_code == 400
    await db.refresh(admin_user)
    assert admin_user.is_active is True


async def test_cannot_deactivate_last_active_admin(db, client, admin_user):
    # Actor is an inactive admin (only reachable via a stale override), so the target is
    # the only active admin.
    actor = User(email="a2@x", display_name="A2", role="admin", username="a2", is_active=False)
    db.add(actor)
    await db.commit()
    r = await _as(actor, lambda: client.patch(f"{ADM}/{admin_user.id}", json={"is_active": False}))
    assert r.status_code == 409 and r.json()["code"] == "conflict"
    await db.refresh(admin_user)
    assert admin_user.is_active is True


async def test_deactivate_denies_pending_requests(db, client, admin_user, regular_user, kiosk):
    r = await client.post(f"{D}/approve-request", json={"user_id": str(regular_user.id)},
                          headers=_h())
    assert r.status_code == 200
    await _as(admin_user, lambda: client.patch(
        f"{ADM}/{regular_user.id}", json={"is_active": False}))
    rows = (await db.execute(select(DeviceAuthorization).execution_options(
        populate_existing=True))).scalars().all()
    assert [x.status for x in rows] == ["denied"]


# ---- kiosk eligibility ----

async def test_kiosk_users_list_filters(db, client, kiosk, admin_user, regular_user):
    third = User(email="t@x", display_name="T", role="user", username="t3", kiosk_enabled=False)
    fourth = User(email="f@x", display_name="F", role="user", username="f4", is_active=False)
    db.add_all([third, fourth])
    await db.commit()
    got = {u["id"] for u in (await client.get(f"{A}/kiosk-login/users", headers=_h())).json()}
    assert got == {str(admin_user.id), str(regular_user.id)}
    await _flag(db, regular_user, kiosk_enabled=False)
    got = {u["id"] for u in (await client.get(f"{A}/kiosk-login/users", headers=_h())).json()}
    assert got == {str(admin_user.id)}


async def test_approve_request_404_for_ineligible(db, client, kiosk, regular_user):
    unknown = await client.post(f"{D}/approve-request", json={"user_id": str(uuid.uuid4())},
                                headers=_h())
    for kw in ({"kiosk_enabled": False}, {"kiosk_enabled": True, "is_active": False}):
        await _flag(db, regular_user, **kw)
        r = await client.post(f"{D}/approve-request", json={"user_id": str(regular_user.id)},
                              headers=_h())
        assert r.status_code == 404 and r.json()["detail"] == unknown.json()["detail"]


async def test_pin_ineligible_same_as_unknown(db, client, kiosk, regular_user, monkeypatch):
    calls = []
    real = kl.dummy_verify
    monkeypatch.setattr(kl, "dummy_verify", lambda: (calls.append(1), real())[1])
    regular_user.kiosk_pin_hash = _ph.hash("123456")
    await db.commit()

    def pin(uid):
        return client.post(f"{D}/pin", json={"user_id": str(uid), "pin": "123456"}, headers=_h())

    ok = await pin(regular_user.id)
    assert ok.status_code == 200 and not calls
    unknown = await pin(uuid.uuid4())
    await _flag(db, regular_user, kiosk_enabled=False)
    r1 = await pin(regular_user.id)
    await _flag(db, regular_user, kiosk_enabled=True, is_active=False)
    r2 = await pin(regular_user.id)
    assert r1.status_code == r2.status_code == unknown.status_code == 403
    assert r1.json()["detail"] == r2.json()["detail"] == unknown.json()["detail"]
    assert len(calls) == 3
    tokens = (await db.execute(select(APIToken).where(APIToken.user_id == regular_user.id))).all()
    assert len(tokens) == 1  # only the first, eligible sign-in minted


async def test_pending_approval_blocked_after_flag_flip(db, client, kiosk, regular_user):
    r = await client.post(f"{D}/approve-request", json={"user_id": str(regular_user.id)},
                          headers=_h())
    code = r.json()["device_code"]
    row = (await db.execute(select(DeviceAuthorization))).scalar_one()
    await _flag(db, regular_user, kiosk_enabled=False)
    a = await _as(regular_user, lambda: client.post(
        f"{D}/requests/{row.id}/approve", json={"match_code": row.match_code}))
    assert a.status_code == 404
    await db.execute(DeviceAuthorization.__table__.update().values(last_polled_at=None))
    await db.commit()
    p = await client.post(f"{D}/token", json={"device_code": code}, headers=_h())
    assert p.status_code == 400 and p.json()["code"] == "access_denied"


async def test_token_poll_blocked_after_flag_flip(db, client, kiosk, regular_user):
    r = await client.post(f"{D}/approve-request", json={"user_id": str(regular_user.id)},
                          headers=_h())
    code = r.json()["device_code"]
    row = (await db.execute(select(DeviceAuthorization))).scalar_one()
    a = await _as(regular_user, lambda: client.post(
        f"{D}/requests/{row.id}/approve", json={"match_code": row.match_code}))
    assert a.status_code == 200
    await _flag(db, regular_user, kiosk_enabled=False)
    await db.execute(DeviceAuthorization.__table__.update().values(last_polled_at=None))
    await db.commit()
    p = await client.post(f"{D}/token", json={"device_code": code}, headers=_h())
    assert p.status_code == 400 and p.json()["code"] == "access_denied"
    n = (await db.execute(select(APIToken).where(APIToken.user_id == regular_user.id))).all()
    assert n == []


# ---- review fixes ----

class _FakeWS:
    def __init__(self):
        self.closed = None

    async def close(self, code=1000):
        self.closed = code


def _reg(user, *channels):
    ws = _FakeWS()
    for ch in channels:
        ws_manager.active_connections[ch].append(ws)
    ws_manager._identities[ws] = SimpleNamespace(user=user, permissions=None)
    return ws


async def test_close_user_closes_only_that_users_sockets(regular_user, admin_user):
    mine, other = _reg(regular_user, "jobs", "notifications"), _reg(admin_user, "jobs")
    await ws_manager.close_user(regular_user.id)
    assert mine.closed == 1008 and other.closed is None
    assert mine not in ws_manager.active_connections.get("jobs", [])
    assert mine not in ws_manager.active_connections.get("notifications", [])
    ws_manager.disconnect("jobs", other)


async def test_deactivate_and_delete_close_sockets(db, client, admin_user, regular_user):
    ws = _reg(regular_user, "scans")
    await _as(admin_user, lambda: client.patch(
        f"{ADM}/{regular_user.id}", json={"is_active": False}))
    assert ws.closed == 1008
    ws2 = _reg(regular_user, "jobs")
    r = await _as(admin_user, lambda: client.delete(f"{ADM}/{regular_user.id}"))
    assert r.status_code == 204 and ws2.closed == 1008


async def test_last_admin_guard_rejects_stale_actor(db, client, admin_user):
    stale = User(email="st@x", display_name="St", role="admin", username="st", is_active=False)
    other = User(email="o2@x", display_name="O2", role="admin", username="o2")
    db.add_all([stale, other])
    await db.commit()
    r = await _as(stale, lambda: client.patch(f"{ADM}/{other.id}", json={"is_active": False}))
    assert r.status_code == 403
    await db.refresh(other)
    assert other.is_active is True


async def test_kiosk_disable_denies_pending_and_revokes_minted_tokens(
        db, client, kiosk, admin_user, regular_user):
    regular_user.kiosk_pin_hash = _ph.hash("123456")
    await db.commit()
    r = await client.post(f"{D}/pin", json={"user_id": str(regular_user.id), "pin": "123456"},
                          headers=_h())
    minted = r.json()["access_token"]
    other_tok = await _tok(db, regular_user, name="manual")
    await client.post(f"{D}/approve-request", json={"user_id": str(regular_user.id)},
                      headers=_h())
    assert (await client.get(f"{A}/me", headers=_h(minted))).status_code == 200
    await _as(admin_user, lambda: client.patch(
        f"{ADM}/{regular_user.id}", json={"kiosk_enabled": False}))
    assert (await client.get(f"{A}/me", headers=_h(minted))).status_code == 401
    assert (await client.get(f"{A}/me", headers=_h(other_tok))).status_code == 200
    rows = (await db.execute(select(DeviceAuthorization).execution_options(
        populate_existing=True))).scalars().all()
    assert {x.status for x in rows if x.target_user_id} == {"denied"}


async def test_qr_approve_refused_when_kiosk_disabled(db, client, kiosk, regular_user):
    row = DeviceAuthorization(
        device_code_hash=hash_token("dc"), user_code="ABCD-EFGH", client_name="K",
        status="pending", interval=5, requester_token_id=kiosk.id,
        expires_at=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc) + __import__("datetime").timedelta(minutes=5))
    db.add(row)
    await _flag(db, regular_user, kiosk_enabled=False)
    r = await _as(regular_user, lambda: client.post(
        f"{D}/approve", json={"user_code": "ABCD-EFGH", "approve": True}))
    assert r.status_code == 403
    assert r.json()["detail"] == "Kiosk sign-in is disabled for your account"
    d = await _as(regular_user, lambda: client.post(
        f"{D}/approve", json={"user_code": "ABCD-EFGH", "approve": False}))
    assert d.status_code == 200
