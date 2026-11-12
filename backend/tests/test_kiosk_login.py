# ruff: noqa: E501
"""Kiosk sign-in: tap-to-approve, PIN login + lockout, PIN management, settings."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from starlette.testclient import TestClient

from app.auth.dependencies import get_current_user
from app.auth.tokens import hash_token
from app.main import app
from app.models import APIToken, AuditEntry, DeviceAuthorization, Kiosk, User
from app.routers import device as device_router
from app.routers.auth import _ph
from app.services import kiosk_login as kl
from app.services import settings_cache
from app.services.ws_manager import ws_manager

A = "/api/v1/auth"
D = "/api/v1/auth/device"
KIOSK = "pprs_kiosk_login_token"


def _h(tok=KIOSK):
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture(autouse=True)
def _reset():
    device_router._approve_request_limiter.clear()
    device_router._approve_limiter.clear()
    for k in ("kiosk_approve_enabled", "kiosk_pin_enabled", "kiosk_pin_length"):
        settings_cache.invalidate(k)
    yield
    for k in ("kiosk_approve_enabled", "kiosk_pin_enabled", "kiosk_pin_length"):
        settings_cache.invalidate(k)


def _setting(**kw):
    for k, v in kw.items():
        settings_cache.put(k, str(v).lower() if isinstance(v, bool) else str(v))


@pytest_asyncio.fixture(loop_scope="function")
async def kiosk(db, admin_user):
    t = APIToken(user_id=admin_user.id, name="k", token_hash=hash_token(KIOSK),
                 permissions=["device"])
    db.add(t)
    await db.commit()
    return t


@pytest.fixture
def pushed(monkeypatch):
    sent = []

    async def fake(user_id, message):
        sent.append((user_id, message))

    monkeypatch.setattr(ws_manager, "broadcast_user", fake)
    return sent


async def _as(user, fn):
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        return await fn()
    finally:
        app.dependency_overrides.clear()


async def _req(client, user, name="Lobby"):
    return await client.post(f"{D}/approve-request",
                             json={"user_id": str(user.id), "client_name": name}, headers=_h())


async def _poll(client, db, code):
    await db.execute(DeviceAuthorization.__table__.update().values(last_polled_at=None))
    await db.commit()
    return await client.post(f"{D}/token", json={"device_code": code}, headers=_h())


async def _row(db, **where):
    rows = (await db.execute(select(DeviceAuthorization).execution_options(
        populate_existing=True))).scalars().all()
    return [SimpleNamespace(**{c.name: getattr(r, c.name) for c in r.__table__.columns})
            for r in rows]


async def _audit(db, action):
    return (await db.execute(select(AuditEntry).where(AuditEntry.action == action))).scalars().all()


# ---- config / users ----

async def test_config_defaults_and_values(db, client, kiosk):
    r = await client.get(f"{A}/kiosk-login/config", headers=_h())
    assert r.json() == {"approve": False, "pin": False, "pin_length": 6}
    _setting(kiosk_approve_enabled=True, kiosk_pin_enabled=True, kiosk_pin_length=8)
    assert (await client.get(f"{A}/kiosk-login/config", headers=_h())).json() == {
        "approve": True, "pin": True, "pin_length": 8}


async def test_config_and_users_need_device_permission(db, client, admin_user):
    db.add(APIToken(user_id=admin_user.id, name="p", token_hash=hash_token("pprs_p"),
                    permissions=["print"]))
    await db.commit()
    for path in ("config", "users"):
        assert (await client.get(f"{A}/kiosk-login/{path}", headers=_h("pprs_p"))).status_code == 403
        assert (await client.get(f"{A}/kiosk-login/{path}")).status_code == 401


async def test_users_excludes_kiosk_service_users(db, client, kiosk, admin_user, regular_user):
    svc = User(email="s@x", display_name="Svc", role="user", username="kiosk-lobby")
    db.add(svc)
    await db.commit()
    db.add(Kiosk(name="lobby", token_id=kiosk.id, service_user_id=svc.id))
    regular_user.kiosk_pin_hash = _ph.hash("123456")
    await db.commit()
    r = await client.get(f"{A}/kiosk-login/users", headers=_h())
    got = {u["id"]: u for u in r.json()}
    assert set(got) == {str(admin_user.id), str(regular_user.id)}
    assert got[str(regular_user.id)]["has_pin"] is True
    assert got[str(admin_user.id)]["has_pin"] is False
    assert set(got[str(admin_user.id)]) == {"id", "display_name", "has_pin"}


# ---- PIN management ----

async def test_set_pin_validation_and_clear(db, client, regular_user):
    _setting(kiosk_pin_length=6)
    for bad in ("12345", "1234567", "12345a", "١٢٣٤٥٦", ""):
        r = await _as(regular_user, lambda: client.put(f"{A}/me/kiosk-pin", json={"pin": bad}))
        assert r.status_code == 422, bad
    regular_user.kiosk_pin_failures = 3
    regular_user.kiosk_pin_locked_until = datetime.now(timezone.utc) + timedelta(minutes=5)
    await db.commit()
    r = await _as(regular_user, lambda: client.put(f"{A}/me/kiosk-pin", json={"pin": "123456"}))
    assert r.status_code == 200
    await db.refresh(regular_user)
    assert regular_user.kiosk_pin_hash and regular_user.kiosk_pin_hash != "123456"
    assert regular_user.kiosk_pin_hash.startswith("$argon2")
    assert regular_user.kiosk_pin_failures == 0 and regular_user.kiosk_pin_locked_until is None
    st = await _as(regular_user, lambda: client.get(f"{A}/me/kiosk-pin"))
    assert st.json() == {"enabled": False, "pin_length": 6, "has_pin": True}
    assert (await _as(regular_user, lambda: client.delete(f"{A}/me/kiosk-pin"))).status_code == 200
    await db.refresh(regular_user)
    assert regular_user.kiosk_pin_hash is None


async def test_pin_endpoints_session_only(db, client, kiosk, regular_user):
    db.add(APIToken(user_id=regular_user.id, name="u", token_hash=hash_token("pprs_u"),
                    permissions=["print"]))
    await db.commit()
    r = await client.put(f"{A}/me/kiosk-pin", json={"pin": "123456"}, headers=_h("pprs_u"))
    assert r.status_code == 403
    assert (await client.put(f"{A}/me/kiosk-pin", json={"pin": "123456"})).status_code == 401


async def test_length_change_clears_all_pins(db, admin_client, admin_user, regular_user):
    _setting(kiosk_pin_length=6)
    for u in (admin_user, regular_user):
        u.kiosk_pin_hash = _ph.hash("123456")
        u.kiosk_pin_consecutive_failures = 2
    await db.commit()
    # same length: untouched
    assert (await admin_client.put("/api/v1/settings", json={"kiosk_pin_length": 6})).status_code == 200
    await db.refresh(admin_user)
    assert admin_user.kiosk_pin_hash
    # explicit store of 6 then change to 8
    assert (await admin_client.put("/api/v1/settings", json={"kiosk_pin_length": 8})).status_code == 200
    for u in (admin_user, regular_user):
        await db.refresh(u)
        assert u.kiosk_pin_hash is None and u.kiosk_pin_consecutive_failures == 0
    cfg = (await admin_client.get("/api/v1/settings")).json()
    assert cfg["kiosk_pin_length"] == 8 and cfg["kiosk_pin_enabled"] is False
    for bad in (3, 13, "abc"):
        r = await admin_client.put("/api/v1/settings", json={"kiosk_pin_length": bad})
        assert r.status_code == 400, bad


# ---- tap to approve ----

async def test_approve_request_disabled_404_and_permission(db, client, kiosk, regular_user):
    assert (await _req(client, regular_user)).status_code == 403
    _setting(kiosk_approve_enabled=True)
    import uuid
    r = await client.post(f"{D}/approve-request", json={"user_id": str(uuid.uuid4())},
                          headers=_h())
    assert r.status_code == 404
    r = await client.post(f"{D}/approve-request", json={"user_id": str(regular_user.id)})
    assert r.status_code == 401


async def test_approve_request_shape_choices_and_push(db, client, kiosk, regular_user, pushed):
    _setting(kiosk_approve_enabled=True)
    r = await _req(client, regular_user)
    assert r.status_code == 200, r.text
    b = r.json()
    assert set(b) == {"device_code", "match_code", "expires_in", "interval"}
    assert 10 <= b["match_code"] <= 99 and b["expires_in"] == 300 and b["interval"] == 5
    (row,) = await _row(db)
    assert row.target_user_id == regular_user.id and row.device_code_hash == hash_token(
        b["device_code"])
    assert len(set(row.choices)) == 3 and b["match_code"] in row.choices
    assert all(10 <= c <= 99 for c in row.choices)
    (uid, msg), = pushed
    assert uid == regular_user.id and msg["type"] == "kiosk_login_request"
    assert msg["data"]["id"] == str(row.id) and msg["data"]["choices"] == row.choices
    assert msg["data"]["client_name"] == "Lobby" and msg["data"]["expires_at"]
    assert await _audit(db, "device.approve_request")


async def test_approve_request_rate_limited(db, client, kiosk, regular_user, pushed):
    _setting(kiosk_approve_enabled=True)
    for _ in range(5):
        assert (await _req(client, regular_user)).status_code == 200
    r = await _req(client, regular_user)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1


async def test_new_request_replaces_old(db, client, kiosk, regular_user, pushed):
    _setting(kiosk_approve_enabled=True)
    first = (await _req(client, regular_user)).json()
    second = (await _req(client, regular_user)).json()
    r = await _poll(client, db, first["device_code"])
    assert r.json()["code"] == "access_denied"
    r = await _poll(client, db, second["device_code"])
    assert r.json()["code"] == "authorization_pending"
    kinds = [m["type"] for _, m in pushed]
    assert kinds == ["kiosk_login_request", "kiosk_login_resolved", "kiosk_login_request"]
    pend = await _as(regular_user, lambda: client.get(f"{D}/requests"))
    assert len(pend.json()) == 1


async def test_right_pick_approves_and_mints(db, client, kiosk, regular_user, pushed):
    _setting(kiosk_approve_enabled=True)
    c = (await _req(client, regular_user)).json()
    (row,) = await _row(db)
    pend = await _as(regular_user, lambda: client.get(f"{D}/requests"))
    assert set(pend.json()[0]) == {"id", "client_name", "choices", "expires_at"}
    r = await _as(regular_user, lambda: client.post(
        f"{D}/requests/{row.id}/approve", json={"match_code": c["match_code"]}))
    assert r.status_code == 200 and r.json() == {"status": "approved"}
    assert pushed[-1][1] == {"type": "kiosk_login_resolved", "data": {"id": str(row.id)}}
    r = await _poll(client, db, c["device_code"])
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["user"]["id"] == str(regular_user.id)
    tok = (await db.execute(select(APIToken).where(APIToken.id == b["token_id"]))).scalar_one()
    assert tok.user_id == regular_user.id
    # kiosk can revoke the minted token
    rv = await client.delete(f"{A}/tokens/{b['token_id']}", headers=_h())
    assert rv.status_code in (200, 204)
    assert await _audit(db, "device.approve")


async def test_wrong_pick_denies_with_409(db, client, kiosk, regular_user, pushed):
    _setting(kiosk_approve_enabled=True)
    c = (await _req(client, regular_user)).json()
    (row,) = await _row(db)
    wrong = next(x for x in row.choices if x != c["match_code"])
    r = await _as(regular_user, lambda: client.post(
        f"{D}/requests/{row.id}/approve", json={"match_code": wrong}))
    assert r.status_code == 409 and r.json()["code"] == "match_code_mismatch"
    assert r.json()["request_id"] is not None
    assert (await _poll(client, db, c["device_code"])).json()["code"] == "access_denied"
    assert await _audit(db, "device.deny")
    # already handled
    r = await _as(regular_user, lambda: client.post(
        f"{D}/requests/{row.id}/approve", json={"match_code": c["match_code"]}))
    assert r.status_code == 404


async def test_deny_endpoint_and_other_user_404(db, client, kiosk, regular_user, admin_user, pushed):
    _setting(kiosk_approve_enabled=True)
    c = (await _req(client, regular_user)).json()
    (row,) = await _row(db)
    for path, body in ((f"{D}/requests/{row.id}/deny", None),
                       (f"{D}/requests/{row.id}/approve", {"match_code": c["match_code"]})):
        r = await _as(admin_user, lambda: client.post(path, json=body))
        assert r.status_code == 404
    other = await _as(admin_user, lambda: client.get(f"{D}/requests"))
    assert other.json() == []
    r = await _as(regular_user, lambda: client.post(f"{D}/requests/{row.id}/deny"))
    assert r.json() == {"status": "denied"}
    assert (await _poll(client, db, c["device_code"])).json()["code"] == "access_denied"


async def test_expired_request_hidden_and_poll_expired(db, client, kiosk, regular_user, pushed):
    _setting(kiosk_approve_enabled=True)
    c = (await _req(client, regular_user)).json()
    await db.execute(DeviceAuthorization.__table__.update().values(
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
    await db.commit()
    assert (await _as(regular_user, lambda: client.get(f"{D}/requests"))).json() == []
    assert (await _poll(client, db, c["device_code"])).json()["code"] == "expired_token"


async def test_bound_request_not_reachable_via_user_code(db, client, kiosk, regular_user, admin_user,
                                                         pushed):
    _setting(kiosk_approve_enabled=True)
    await _req(client, regular_user)
    (row,) = await _row(db)
    r = await _as(admin_user, lambda: client.post(
        f"{D}/approve", json={"user_code": row.user_code, "approve": True}))
    assert r.status_code == 404


async def test_requests_session_only(db, client, kiosk):
    assert (await client.get(f"{D}/requests", headers=_h())).status_code == 403


# ---- PIN login ----

async def _pin_user(db, user, pin="123456"):
    user.kiosk_pin_hash = _ph.hash(pin)
    await db.commit()


def _pin(client, user_id, pin, name="Lobby"):
    return client.post(f"{D}/pin", json={"user_id": str(user_id), "pin": pin,
                                         "client_name": name}, headers=_h())


async def test_pin_success_token_same_shape(db, client, kiosk, regular_user):
    _setting(kiosk_pin_enabled=True)
    await _pin_user(db, regular_user)
    regular_user.kiosk_pin_failures = 2
    regular_user.kiosk_pin_consecutive_failures = 2
    await db.commit()
    r = await _pin(client, regular_user.id, "123456")
    assert r.status_code == 200, r.text
    b = r.json()
    assert set(b) == {"access_token", "token_id", "expires_at", "scopes", "user"}
    assert b["scopes"] == ["print", "scan", "files", "email"]
    exp = datetime.fromisoformat(b["expires_at"])
    assert timedelta(minutes=14) < exp - datetime.now(timezone.utc) <= timedelta(minutes=15)
    tok = (await db.execute(select(APIToken).where(APIToken.id == b["token_id"]))).scalar_one()
    assert tok.user_id == regular_user.id and tok.name == "Device: Lobby"
    (row,) = await _row(db)
    assert row.minted_token_id == tok.id
    me = await client.get(f"{A}/me", headers=_h(b["access_token"]))
    assert me.json()["id"] == str(regular_user.id)
    assert (await client.delete(f"{A}/tokens/{b['token_id']}", headers=_h())).status_code in (200, 204)
    await db.refresh(regular_user)
    assert regular_user.kiosk_pin_failures == 0 and regular_user.kiosk_pin_consecutive_failures == 0
    assert await _audit(db, "kiosk.pin_login")


async def test_pin_403_cases_run_dummy_verify(db, client, kiosk, regular_user, admin_user,
                                              monkeypatch):
    calls = []
    real = kl.dummy_verify
    monkeypatch.setattr(kl, "dummy_verify", lambda: (calls.append(1), real())[1])
    r = await _pin(client, regular_user.id, "123456")  # disabled
    assert r.status_code == 403 and not calls
    _setting(kiosk_pin_enabled=True)
    import uuid
    r1 = await _pin(client, regular_user.id, "123456")  # no PIN
    r2 = await _pin(client, uuid.uuid4(), "123456")  # unknown
    assert r1.status_code == r2.status_code == 403
    assert r1.json()["detail"] == r2.json()["detail"] and len(calls) == 2


async def test_pin_requires_device_permission(db, client, regular_user):
    r = await client.post(f"{D}/pin", json={"user_id": str(regular_user.id), "pin": "1"})
    assert r.status_code == 401


async def test_pin_wrong_then_lock_at_5(db, client, kiosk, regular_user):
    _setting(kiosk_pin_enabled=True)
    await _pin_user(db, regular_user)
    for i in range(4):
        r = await _pin(client, regular_user.id, "000000")
        assert r.status_code == 401
        b = r.json()
        assert b["remaining"] == 4 - i and b["code"] == "pin_incorrect"
        assert b["detail"] and b["request_id"]
    r = await _pin(client, regular_user.id, "000000")
    assert r.status_code == 423 and r.json()["code"] == "pin_locked"
    assert r.json()["retry_after"] == 900 and r.headers["Retry-After"] == "900"
    # locked even with the right PIN
    r = await _pin(client, regular_user.id, "123456")
    assert r.status_code == 423 and 1 <= r.json()["retry_after"] <= 900
    assert await _audit(db, "kiosk.pin_locked") and len(await _audit(db, "kiosk.pin_failed")) == 4
    # lock expiry: right PIN works again and resets everything
    await db.refresh(regular_user)
    regular_user.kiosk_pin_locked_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db.commit()
    assert (await _pin(client, regular_user.id, "123456")).status_code == 200
    await db.refresh(regular_user)
    assert regular_user.kiosk_pin_consecutive_failures == 0


async def test_pin_cleared_at_10_consecutive(db, client, kiosk, regular_user, pushed, monkeypatch):
    _setting(kiosk_pin_enabled=True)
    await _pin_user(db, regular_user)
    mail = AsyncMock()
    from app.services import email_service as es
    monkeypatch.setattr(es.email_service, "send_alert", mail)
    for _ in range(5):
        await _pin(client, regular_user.id, "000000")
    # simulate lock expiry
    regular_user.kiosk_pin_locked_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db.commit()
    for i in range(4):
        r = await _pin(client, regular_user.id, "000000")
        assert r.status_code == 401, i
    r = await _pin(client, regular_user.id, "000000")  # 10th
    assert r.status_code == 401 and r.json()["remaining"] == 0
    await db.refresh(regular_user)
    assert regular_user.kiosk_pin_hash is None and regular_user.kiosk_pin_consecutive_failures == 0
    assert pushed[-1][0] == regular_user.id and pushed[-1][1]["type"] == "kiosk_pin_cleared"
    mail.assert_awaited_once()
    assert mail.await_args.args[1] == regular_user.email
    assert await _audit(db, "kiosk.pin_cleared")
    assert (await _pin(client, regular_user.id, "123456")).status_code == 403


async def test_pin_clear_survives_email_failure(db, client, kiosk, regular_user, monkeypatch):
    _setting(kiosk_pin_enabled=True)
    await _pin_user(db, regular_user)
    regular_user.kiosk_pin_consecutive_failures = 9
    await db.commit()
    from app.services import email_service as es
    monkeypatch.setattr(es.email_service, "send_alert", AsyncMock(side_effect=RuntimeError))
    assert (await _pin(client, regular_user.id, "0")).status_code == 401
    await db.refresh(regular_user)
    assert regular_user.kiosk_pin_hash is None


# ---- WS fan-out ----

async def test_broadcast_user_targets_only_that_user(db, regular_user, admin_user):
    class Sock:
        def __init__(self):
            self.got = []

        async def send_json(self, m):
            self.got.append(m)

    class Ident:
        def __init__(self, u):
            self.user = u
            self.permissions = None

    mine, theirs = Sock(), Sock()
    ws_manager.active_connections["notifications"] = [mine, theirs]
    ws_manager._identities[mine] = Ident(regular_user)
    ws_manager._identities[theirs] = Ident(admin_user)
    try:
        await ws_manager.broadcast_user(regular_user.id, {"type": "x"})
    finally:
        ws_manager.active_connections.pop("notifications", None)
        ws_manager._identities.pop(mine, None)
        ws_manager._identities.pop(theirs, None)
    assert mine.got == [{"type": "x"}] and theirs.got == []


def test_notifications_ws_rejects_unauthenticated():
    from starlette.websockets import WebSocketDisconnect
    c = TestClient(app)
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/api/v1/ws/notifications"):
            pass


