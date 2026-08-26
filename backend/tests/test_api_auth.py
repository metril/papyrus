"""API auth suite — real token path, session overrides, and role/permission gates.

These exercise the actual dependency chain over the ASGI app (no auth mocking
for the token cases): get_current_user -> validate_token -> require_admin /
require_permission. 401/403 codes here are a frontend contract.
"""
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher

from app.auth.tokens import hash_token
from app.models import APIToken, User

_ph = PasswordHasher()


async def _seed_user(db, *, role="user", email="tok@example.com", username="tok") -> User:
    user = User(email=email, display_name="Tok", role=role, is_local=True, username=username)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _seed_token(
    db, user, *, plaintext, permissions, expires_at=None, name="test-token"
) -> APIToken:
    token = APIToken(
        user_id=user.id,
        name=name,
        token_hash=hash_token(plaintext),
        permissions=permissions,
        expires_at=expires_at,
    )
    db.add(token)
    await db.commit()
    await db.refresh(token)
    return token


async def test_unauthenticated_request_to_protected_route_is_401(client):
    resp = await client.get("/api/settings")
    assert resp.status_code == 401


async def test_regular_user_on_admin_route_is_403(user_client):
    resp = await user_client.get("/api/settings")
    assert resp.status_code == 403


async def test_valid_token_grants_access_and_updates_last_used(db, client):
    user = await _seed_user(db, role="user")
    plaintext = "pprs_test_valid_token_value"
    token = await _seed_token(db, user, plaintext=plaintext, permissions=["print"])
    assert token.last_used_at is None

    resp = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {plaintext}"})
    assert resp.status_code == 200
    assert resp.json()["email"] == user.email

    # validate_token stamps last_used_at on its own session + commits; refresh
    # to observe the committed change from this fixture's session.
    await db.refresh(token)
    assert token.last_used_at is not None


async def test_expired_token_is_401(db, client):
    user = await _seed_user(db, role="admin")
    plaintext = "pprs_test_expired_token"
    await _seed_token(
        db,
        user,
        plaintext=plaintext,
        permissions=["admin"],
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    resp = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {plaintext}"})
    assert resp.status_code == 401


async def test_garbage_token_is_401(client):
    resp = await client.get(
        "/api/auth/me", headers={"Authorization": "Bearer pprs_not_a_real_token"}
    )
    assert resp.status_code == 401


async def test_admin_role_token_missing_admin_permission_is_403(db, client):
    """Admin-role user, but the token's permission list omits 'admin'."""
    user = await _seed_user(db, role="admin")
    plaintext = "pprs_test_admin_role_no_admin_perm"
    await _seed_token(db, user, plaintext=plaintext, permissions=["print"])
    resp = await client.get("/api/settings", headers={"Authorization": f"Bearer {plaintext}"})
    assert resp.status_code == 403


async def test_token_permission_scope_enforced_on_permission_route(db, client):
    """Token lacking 'print' is rejected by require_permission('print') ..."""
    user = await _seed_user(db, role="user")
    no_print = "pprs_test_scan_only"
    await _seed_token(db, user, plaintext=no_print, permissions=["scan"], name="scan-only")
    resp = await client.get("/api/jobs", headers={"Authorization": f"Bearer {no_print}"})
    assert resp.status_code == 403

    # ... and a token that has 'print' is allowed through.
    has_print = "pprs_test_print_ok"
    await _seed_token(db, user, plaintext=has_print, permissions=["print"], name="print-ok")
    resp = await client.get("/api/jobs", headers={"Authorization": f"Bearer {has_print}"})
    assert resp.status_code == 200


# --------------------------------------------------------------------------- #
# Local login (F67 async offload, F68 throttle + constant-time unknown user)
# --------------------------------------------------------------------------- #
async def _seed_local_user(
    db, *, username="localuser", password="correct-horse-battery", role="user"
) -> User:
    user = User(
        email=f"{username}@example.com",
        display_name=username.title(),
        role=role,
        is_local=True,
        username=username,
        password_hash=_ph.hash(password),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def test_local_login_with_correct_password_succeeds(db, client):
    password = "s3cure-test-password"
    user = await _seed_local_user(db, username="loginok", password=password)

    resp = await client.post(
        "/api/auth/local-login", json={"username": "loginok", "password": password}
    )
    assert resp.status_code == 200
    assert resp.json() == {"message": "logged in", "user_id": str(user.id)}


async def test_local_login_with_wrong_password_is_401(db, client):
    await _seed_local_user(db, username="loginwrongpw", password="the-real-password")

    resp = await client.post(
        "/api/auth/local-login",
        json={"username": "loginwrongpw", "password": "not-the-password"},
    )
    assert resp.status_code == 401


async def test_local_login_with_unknown_username_is_401(client):
    """F68: an unknown username must not short-circuit before argon2 runs —
    it still verifies against a dummy hash so the response looks the same as
    a wrong-password attempt on a real user, both in status code and (by
    construction) in the argon2 work performed."""
    resp = await client.post(
        "/api/auth/local-login",
        json={"username": "does-not-exist", "password": "whatever"},
    )
    assert resp.status_code == 401


async def test_local_login_throttled_after_five_failed_attempts(db, client):
    """Regression (F68): local-login had no rate limit or lockout, so an
    attacker could script password guesses at full request rate. After 5
    failures from the same (ip, username) pair, the 6th attempt must 429
    even with the correct password."""
    password = "the-real-password"
    await _seed_local_user(db, username="throttleuser", password=password)

    for _ in range(5):
        resp = await client.post(
            "/api/auth/local-login",
            json={"username": "throttleuser", "password": "wrong-guess"},
        )
        assert resp.status_code == 401

    locked_resp = await client.post(
        "/api/auth/local-login",
        json={"username": "throttleuser", "password": password},
    )
    assert locked_resp.status_code == 429


async def test_local_login_throttle_is_scoped_per_username(db, client):
    """A different username from the same client is unaffected by another
    username's lockout — the throttle key includes the username, not just
    the caller's IP."""
    password = "the-real-password"
    await _seed_local_user(db, username="lockedout", password=password)
    await _seed_local_user(db, username="unaffected", password=password)

    for _ in range(5):
        resp = await client.post(
            "/api/auth/local-login",
            json={"username": "lockedout", "password": "wrong-guess"},
        )
        assert resp.status_code == 401

    ok_resp = await client.post(
        "/api/auth/local-login", json={"username": "unaffected", "password": password}
    )
    assert ok_resp.status_code == 200
