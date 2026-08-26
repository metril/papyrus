"""Tests for API token generation, hashing, validation, and the token routes."""
import uuid

from sqlalchemy import select

from app.auth.tokens import generate_token, hash_token, validate_token
from app.models import APIToken, User


def test_generate_token_format():
    plaintext, token_hash = generate_token()
    assert plaintext.startswith("pprs_")
    assert len(token_hash) == 64  # SHA-256 hex digest


def test_generate_token_unique():
    tokens = [generate_token() for _ in range(10)]
    plaintexts = [t[0] for t in tokens]
    hashes = [t[1] for t in tokens]
    assert len(set(plaintexts)) == 10
    assert len(set(hashes)) == 10


def test_hash_token_deterministic():
    plaintext, expected_hash = generate_token()
    assert hash_token(plaintext) == expected_hash


def test_hash_token_different_inputs():
    assert hash_token("pprs_abc") != hash_token("pprs_def")


# --------------------------------------------------------------------------- #
# validate_token (F66: coarse last_used_at write window)
# --------------------------------------------------------------------------- #
async def _seed_user_and_token(db, *, username="tok-validate") -> tuple[User, str]:
    user = User(
        email=f"{username}@example.com", display_name=username.title(),
        role="user", is_local=True, username=username,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    plaintext, token_hash = generate_token()
    token = APIToken(user_id=user.id, name="t", token_hash=token_hash, permissions=["print"])
    db.add(token)
    await db.commit()
    return user, plaintext


async def test_validate_token_stamps_last_used_at_on_first_use(db):
    _user, plaintext = await _seed_user_and_token(db)

    result = await validate_token(db, plaintext)

    assert result is not None
    assert result.last_used_at is not None


async def test_validate_token_does_not_rewrite_last_used_at_within_60s(db):
    """Regression (F66): validate_token used to unconditionally UPDATE +
    COMMIT last_used_at on every call, turning every read-only API request
    into a write transaction. A second call moments later must not move the
    timestamp."""
    _user, plaintext = await _seed_user_and_token(db)

    first = await validate_token(db, plaintext)
    first_used_at = first.last_used_at

    second = await validate_token(db, plaintext)

    assert second.last_used_at == first_used_at


# --------------------------------------------------------------------------- #
# DELETE /api/auth/tokens/{token_id} (F123: token_id must be a real UUID)
# --------------------------------------------------------------------------- #
async def test_revoke_token_with_malformed_id_is_422_not_500(admin_client):
    """Regression (F123): token_id used to be typed str and compared against
    a UUID column, so asyncpg raised a DBAPIError before the 404 branch —
    surfacing as a generic 500 instead of a validation error."""
    resp = await admin_client.delete("/api/auth/tokens/not-a-uuid")
    assert resp.status_code == 422


async def test_revoke_token_deletes_the_row(db, admin_client, admin_user):
    plaintext, token_hash = generate_token()
    token = APIToken(
        user_id=admin_user.id, name="revoke-me", token_hash=token_hash, permissions=["admin"],
    )
    db.add(token)
    await db.commit()
    await db.refresh(token)

    resp = await admin_client.delete(f"/api/auth/tokens/{token.id}")
    assert resp.status_code == 204

    result = await db.execute(select(APIToken).where(APIToken.id == token.id))
    assert result.scalar_one_or_none() is None


async def test_revoke_nonexistent_but_well_formed_token_id_is_404(admin_client):
    resp = await admin_client.delete(f"/api/auth/tokens/{uuid.uuid4()}")
    assert resp.status_code == 404
