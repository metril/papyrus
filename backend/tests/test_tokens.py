"""Tests for API token generation and hashing."""
from app.auth.tokens import generate_token, hash_token


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
