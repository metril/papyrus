"""Unit tests for ftp_service's pure SFTP host-key-fingerprint helpers
(F58 fix-up).

`paramiko` isn't installed in this dev environment (optional `sftp` extra),
so these exercise only `_normalize_fingerprint`/`_sftp_key_fingerprint`
directly with a tiny stand-in for paramiko's host-key object (just needs an
`asbytes()` method) -- no real SSH connection involved.
"""
import base64
import hashlib

from app.services.ftp_service import _normalize_fingerprint, _sftp_key_fingerprint


class _FakeHostKey:
    """Stand-in for paramiko.PKey: only `asbytes()` is used."""

    def __init__(self, data: bytes):
        self._data = data

    def asbytes(self) -> bytes:
        return self._data


_KEY_BYTES = b"pretend-this-is-an-ssh-host-key-public-blob"


def _padded_digest(data: bytes) -> str:
    """The raw base64.b64encode(sha256(...)) form, WITH "=" padding --
    what a naive re-implementation (and the pre-fix code) would compute."""
    return base64.b64encode(hashlib.sha256(data).digest()).decode()


def test_sftp_key_fingerprint_matches_manual_sha256():
    fp = _sftp_key_fingerprint(_FakeHostKey(_KEY_BYTES))
    assert fp == _padded_digest(_KEY_BYTES).rstrip("=")


def test_sftp_key_fingerprint_has_no_padding():
    """Regression: ssh-keygen -lf emits the unpadded form; the previous
    implementation kept the trailing "=" padding, so a fingerprint pinned by
    copying straight from ssh-keygen never matched."""
    fp = _sftp_key_fingerprint(_FakeHostKey(_KEY_BYTES))
    assert "=" not in fp


def test_normalize_fingerprint_accepts_sha256_prefix():
    fp = _sftp_key_fingerprint(_FakeHostKey(_KEY_BYTES))
    assert _normalize_fingerprint(f"SHA256:{fp}") == fp


def test_normalize_fingerprint_accepts_lowercase_sha256_prefix():
    fp = _sftp_key_fingerprint(_FakeHostKey(_KEY_BYTES))
    assert _normalize_fingerprint(f"sha256:{fp}") == fp


def test_normalize_fingerprint_strips_whitespace():
    fp = _sftp_key_fingerprint(_FakeHostKey(_KEY_BYTES))
    assert _normalize_fingerprint(f"  {fp}\n") == fp


def test_normalize_fingerprint_strips_padding_from_a_padded_pin():
    """An admin pasting the raw (padded) base64 digest -- e.g. computed by
    hand rather than copied from ssh-keygen -- must still match."""
    padded = _padded_digest(_KEY_BYTES)
    assert padded.endswith("=")
    assert _normalize_fingerprint(padded) == _sftp_key_fingerprint(_FakeHostKey(_KEY_BYTES))


def test_padded_and_unpadded_pins_both_match_the_computed_fingerprint():
    computed = _sftp_key_fingerprint(_FakeHostKey(_KEY_BYTES))
    padded_pin = _padded_digest(_KEY_BYTES)
    unpadded_pin = computed

    assert _normalize_fingerprint(padded_pin) == computed
    assert _normalize_fingerprint(unpadded_pin) == computed
    assert _normalize_fingerprint(f"SHA256:{unpadded_pin}") == computed


def test_normalize_fingerprint_mismatch_for_a_different_key_stays_a_mismatch():
    fp = _sftp_key_fingerprint(_FakeHostKey(_KEY_BYTES))
    other_fp = _sftp_key_fingerprint(_FakeHostKey(b"a completely different key"))
    assert _normalize_fingerprint(fp) != _normalize_fingerprint(other_fp)
