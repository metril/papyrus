"""Tests for `validate_runtime_secrets` (F2): startup must hard-fail when
`PAPYRUS_SESSION_SECRET` is empty or still the `change-me-in-production`
placeholder, since Starlette's `SessionMiddleware` signs auth cookies with
this key (`app.main` adds it with `secret_key=settings.session_secret`) — an
empty/known key lets an attacker forge any user's session.

`app.config.settings` is a module-level singleton imported by reference
elsewhere (`from app.config import settings`), so mutating attributes on the
existing instance via monkeypatch is visible everywhere without needing to
reload any module.
"""
import logging

import pytest

from app.config import settings, validate_runtime_secrets


def test_raises_when_session_secret_is_empty(monkeypatch):
    monkeypatch.setattr(settings, "session_secret", "")
    monkeypatch.setattr(settings, "dev_mode", False)

    with pytest.raises(RuntimeError, match="PAPYRUS_SESSION_SECRET"):
        validate_runtime_secrets()


def test_raises_when_session_secret_is_the_default_placeholder(monkeypatch):
    monkeypatch.setattr(settings, "session_secret", "change-me-in-production")
    monkeypatch.setattr(settings, "dev_mode", False)

    with pytest.raises(RuntimeError, match="PAPYRUS_SESSION_SECRET"):
        validate_runtime_secrets()


def test_only_warns_when_dev_mode_is_enabled(monkeypatch, caplog):
    monkeypatch.setattr(settings, "session_secret", "")
    monkeypatch.setattr(settings, "dev_mode", True)

    with caplog.at_level(logging.WARNING, logger="app.config"):
        validate_runtime_secrets()  # must not raise

    assert any("PAPYRUS_SESSION_SECRET" in record.message for record in caplog.records)


def test_does_not_raise_or_warn_for_a_real_secret(monkeypatch, caplog):
    monkeypatch.setattr(settings, "session_secret", "a-real-random-secret-value")
    monkeypatch.setattr(settings, "dev_mode", False)

    with caplog.at_level(logging.WARNING, logger="app.config"):
        validate_runtime_secrets()  # must not raise

    assert caplog.records == []
