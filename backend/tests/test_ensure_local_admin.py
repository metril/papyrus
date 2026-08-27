"""``main._ensure_local_admin`` -- creates the local admin account from
PAPYRUS_ADMIN_USERNAME/PAPYRUS_ADMIN_PASSWORD on startup, previously
untested.

Regression (final-review Important #3): .env.example used to prefill
PAPYRUS_ADMIN_PASSWORD with a placeholder ("change-me-strong-password") that
someone could plausibly deploy verbatim. It's now blank by default, so the
common shape becomes "username set, password not" -- which must never create
an admin account with an empty password, and should say so clearly in the
logs rather than silently doing nothing.

Regression (residual-review minor): docker/compose.yaml defaults
PAPYRUS_ADMIN_USERNAME to "admin", so "username set, password blank" is the
*ordinary* OIDC-only deployment shape under compose, not a misconfiguration
-- it must log at info, not warning. "password set, username blank" is the
genuine misconfiguration and keeps the warning.
"""
import logging

from sqlalchemy import select

from app.config import settings
from app.main import _ensure_local_admin
from app.models import User


async def test_skips_with_info_when_username_set_but_password_empty(db, monkeypatch, caplog):
    """The ordinary OIDC-only shape under compose (PAPYRUS_ADMIN_USERNAME
    defaults to "admin", password left blank) -- must not create an account
    and must not warn (it's not a misconfiguration)."""
    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(settings, "admin_password", "")

    with caplog.at_level(logging.INFO, logger="app.main"):
        await _ensure_local_admin()

    result = await db.execute(select(User).where(User.username == "admin"))
    assert result.scalar_one_or_none() is None

    assert any(
        "PAPYRUS_ADMIN_PASSWORD" in record.message and record.levelno == logging.INFO
        for record in caplog.records
    )
    assert not any(record.levelno == logging.WARNING for record in caplog.records)


async def test_skips_silently_when_both_unset(db, monkeypatch, caplog):
    """Nothing configured at all -- must not create an account and must not
    warn (it's not a misconfiguration)."""
    monkeypatch.setattr(settings, "admin_username", "")
    monkeypatch.setattr(settings, "admin_password", "")

    with caplog.at_level(logging.INFO, logger="app.main"):
        await _ensure_local_admin()

    result = await db.execute(select(User))
    assert result.scalar_one_or_none() is None
    assert not any(record.levelno == logging.WARNING for record in caplog.records)


async def test_skips_and_warns_when_password_set_but_username_empty(db, monkeypatch, caplog):
    """Password set with no username is the genuine misconfiguration --
    warn instead of silently skipping."""
    monkeypatch.setattr(settings, "admin_username", "")
    monkeypatch.setattr(settings, "admin_password", "a-real-password")

    with caplog.at_level(logging.WARNING, logger="app.main"):
        await _ensure_local_admin()

    result = await db.execute(select(User))
    assert result.scalar_one_or_none() is None

    assert any(
        "PAPYRUS_ADMIN_USERNAME" in record.message and record.levelno == logging.WARNING
        for record in caplog.records
    )


async def test_creates_admin_when_username_and_password_both_set(db, monkeypatch):
    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(settings, "admin_password", "a-real-password")

    await _ensure_local_admin()

    result = await db.execute(select(User).where(User.username == "admin"))
    admin = result.scalar_one_or_none()
    assert admin is not None
    assert admin.role == "admin"
    assert admin.is_local is True
