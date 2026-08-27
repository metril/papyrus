"""``app.services.crypto``'s legacy-plaintext helpers (F5 legacy ruling):
`is_encrypted` distinguishes a real Fernet token from a legacy plaintext
value, and `decrypt_value_lenient` falls back to treating a non-token value
as plaintext instead of raising.
"""
import logging

import pytest
from cryptography.fernet import InvalidToken

import app.services.crypto as crypto_module
from app.services.crypto import decrypt_value, decrypt_value_lenient, encrypt_value, is_encrypted


def test_is_encrypted_true_for_a_real_fernet_token():
    assert is_encrypted(encrypt_value("hunter2")) is True


def test_is_encrypted_false_for_plaintext():
    assert is_encrypted("hunter2") is False


def test_is_encrypted_false_for_empty_string():
    assert is_encrypted("") is False


def test_decrypt_value_lenient_decrypts_a_real_token():
    token = encrypt_value("hunter2")
    assert decrypt_value_lenient(token) == "hunter2"


def test_decrypt_value_lenient_falls_back_to_plaintext(caplog):
    with caplog.at_level(logging.WARNING, logger="app.services.crypto"):
        result = decrypt_value_lenient("legacy-plaintext-secret")

    assert result == "legacy-plaintext-secret"
    assert any("legacy plaintext" in r.message for r in caplog.records)


def test_decrypt_value_lenient_warns_only_once_per_distinct_value(caplog, monkeypatch):
    monkeypatch.setattr(crypto_module, "_warned_legacy_secret_hashes", set())

    with caplog.at_level(logging.WARNING, logger="app.services.crypto"):
        decrypt_value_lenient("same-legacy-secret")
        decrypt_value_lenient("same-legacy-secret")
        decrypt_value_lenient("same-legacy-secret")

    warnings = [r for r in caplog.records if "legacy plaintext" in r.message]
    assert len(warnings) == 1


def test_decrypt_value_still_raises_for_a_non_token_value():
    with pytest.raises(InvalidToken):
        decrypt_value("not-a-real-token")
