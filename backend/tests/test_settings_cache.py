"""Tests for the settings TTL cache and its wiring into get_setting.

Covers: cache-module behaviour in isolation (hit/miss/expiry/invalidate), and
integration with `app.routers.settings.get_setting` — proving a second call
within the TTL does not hit the DB (via a counting fake session), that the
value stored is the post-decryption plaintext, that expiry (monkeypatched
`time.monotonic`) forces a re-query, and that invalidation forces a re-query.
"""
import pytest

from app.routers import settings as settings_router
from app.services import settings_cache


@pytest.fixture(autouse=True)
def _clear_cache():
    """The cache is a module-level dict; isolate each test."""
    settings_cache.invalidate_all()
    yield
    settings_cache.invalidate_all()


class _FakeRow:
    def __init__(self, value: str):
        self.value = value


class _CountingSession:
    """Minimal stand-in for AsyncSession, tracking `.get()` calls."""

    def __init__(self, data: dict[str, str]):
        self.data = data
        self.get_calls = 0

    async def get(self, _model, key: str):
        self.get_calls += 1
        value = self.data.get(key)
        return _FakeRow(value) if value is not None else None


# ---------------------------------------------------------------------------
# settings_cache module in isolation
# ---------------------------------------------------------------------------


def test_miss_on_absent_key():
    hit, value = settings_cache.get("nope")
    assert hit is False
    assert value is None


def test_put_then_get_is_a_hit():
    settings_cache.put("scan_dir", "/data/scans")
    hit, value = settings_cache.get("scan_dir")
    assert hit is True
    assert value == "/data/scans"


def test_put_caches_none_values_too():
    settings_cache.put("missing_key", None)
    hit, value = settings_cache.get("missing_key")
    assert hit is True
    assert value is None


def test_entry_expires_after_ttl(monkeypatch):
    fake_now = [1_000.0]
    monkeypatch.setattr(settings_cache.time, "monotonic", lambda: fake_now[0])

    settings_cache.put("scan_dir", "/data/scans")
    hit, value = settings_cache.get("scan_dir")
    assert (hit, value) == (True, "/data/scans")

    fake_now[0] += settings_cache.TTL_SECONDS - 1
    hit, _ = settings_cache.get("scan_dir")
    assert hit is True  # still within TTL

    fake_now[0] += 2  # now past TTL_SECONDS from the put
    hit, value = settings_cache.get("scan_dir")
    assert hit is False
    assert value is None


def test_invalidate_removes_single_key():
    settings_cache.put("a", "1")
    settings_cache.put("b", "2")
    settings_cache.invalidate("a")
    assert settings_cache.get("a") == (False, None)
    assert settings_cache.get("b") == (True, "2")


def test_invalidate_all_clears_everything():
    settings_cache.put("a", "1")
    settings_cache.put("b", "2")
    settings_cache.invalidate_all()
    assert settings_cache.get("a") == (False, None)
    assert settings_cache.get("b") == (False, None)


# ---------------------------------------------------------------------------
# get_setting integration
# ---------------------------------------------------------------------------


async def test_get_setting_second_call_within_ttl_hits_no_db():
    db = _CountingSession({"scan_dir": "/data/scans"})

    first = await settings_router.get_setting(db, "scan_dir")
    second = await settings_router.get_setting(db, "scan_dir")

    assert first == second == "/data/scans"
    assert db.get_calls == 1


async def test_get_setting_missing_key_is_also_cached():
    db = _CountingSession({})

    first = await settings_router.get_setting(db, "scan_dir")
    second = await settings_router.get_setting(db, "scan_dir")

    assert first is None
    assert second is None
    assert db.get_calls == 1


async def test_get_setting_expiry_forces_requery(monkeypatch):
    fake_now = [2_000.0]
    monkeypatch.setattr(settings_cache.time, "monotonic", lambda: fake_now[0])
    db = _CountingSession({"scan_dir": "/data/scans"})

    await settings_router.get_setting(db, "scan_dir")
    assert db.get_calls == 1

    fake_now[0] += settings_cache.TTL_SECONDS + 1
    await settings_router.get_setting(db, "scan_dir")
    assert db.get_calls == 2


async def test_get_setting_invalidate_forces_requery():
    db = _CountingSession({"scan_dir": "/data/scans"})

    await settings_router.get_setting(db, "scan_dir")
    assert db.get_calls == 1

    settings_cache.invalidate("scan_dir")

    await settings_router.get_setting(db, "scan_dir")
    assert db.get_calls == 2


async def test_get_setting_caches_post_decryption_value(monkeypatch):
    decrypt_calls = 0

    def fake_decrypt(_ciphertext: str) -> str:
        nonlocal decrypt_calls
        decrypt_calls += 1
        return "plaintext-secret"

    monkeypatch.setattr(settings_router, "decrypt_value", fake_decrypt)
    db = _CountingSession({"smtp_password_encrypted": "ciphertext"})

    first = await settings_router.get_setting(db, "smtp_password")
    second = await settings_router.get_setting(db, "smtp_password")

    assert first == second == "plaintext-secret"
    assert db.get_calls == 1
    assert decrypt_calls == 1  # decrypted once; cached value served on 2nd call


async def test_get_setting_decrypt_failure_returns_none_and_is_not_stale_success(monkeypatch):
    def fake_decrypt(_ciphertext: str) -> str:
        raise ValueError("bad key")

    monkeypatch.setattr(settings_router, "decrypt_value", fake_decrypt)
    db = _CountingSession({"smtp_password_encrypted": "ciphertext"})

    result = await settings_router.get_setting(db, "smtp_password")
    assert result is None
