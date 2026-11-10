"""Rate limiter unit tests (fake clock) and middleware integration tests."""
import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.tokens import generate_token
from app.main import app
from app.models import APIToken, User
from app.services import settings_cache
from app.services.rate_limit import RateLimiter, retry_after_seconds


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_burst_then_blocked_then_refill():
    clock = FakeClock()
    rl = RateLimiter(clock=clock)
    assert all(rl.check("k", 60) == 0.0 for _ in range(60))
    wait = rl.check("k", 60)
    assert wait == pytest.approx(1.0)
    clock.now += 1.0
    assert rl.check("k", 60) == 0.0
    assert rl.check("k", 60) > 0


def test_keys_are_independent():
    rl = RateLimiter(clock=FakeClock())
    for _ in range(5):
        rl.check("a", 5)
    assert rl.check("a", 5) > 0
    assert rl.check("b", 5) == 0.0


def test_lru_bound_evicts_oldest():
    rl = RateLimiter(max_keys=3, clock=FakeClock())
    for i in range(10):
        rl.check(f"k{i}", 10)
    assert len(rl._buckets) == 3
    assert "k9" in rl._buckets and "k0" not in rl._buckets


def test_sweep_drops_refilled_buckets():
    clock = FakeClock()
    rl = RateLimiter(clock=clock)
    for i in range(10):
        rl.check(f"k{i}", 60)
    clock.now += 120
    rl._calls = rl._SWEEP_EVERY - 1
    rl.check("fresh", 60)
    assert list(rl._buckets) == ["fresh"]


def test_retry_after_is_whole_seconds_min_one():
    assert retry_after_seconds(0.2) == 1
    assert retry_after_seconds(1.1) == 2


def _enable(ip_rpm=3, token_rpm=2):
    settings_cache.put("rate_limit_enabled", "true")
    settings_cache.put("rate_limit_ip_rpm", str(ip_rpm))
    settings_cache.put("rate_limit_token_rpm", str(token_rpm))


def _client(ip="203.0.113.5"):
    return AsyncClient(transport=ASGITransport(app=app, client=(ip, 5000)), base_url="http://test")


async def test_ip_bucket_429_envelope_and_retry_after(db):
    _enable(ip_rpm=3)
    async with _client() as c:
        for _ in range(3):
            assert (await c.get("/api/v1/auth/me")).status_code == 401
        r = await c.get("/api/v1/auth/me")
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) >= 1
    body = r.json()
    assert body["code"] == "rate_limited"
    assert body["request_id"] == r.headers["x-request-id"]
    assert body["detail"]


async def test_disabled_never_limits(db):
    _enable(ip_rpm=1)
    settings_cache.put("rate_limit_enabled", "false")
    async with _client() as c:
        for _ in range(5):
            assert (await c.get("/api/v1/auth/me")).status_code == 401


async def test_legacy_alias_path_is_counted(db):
    _enable(ip_rpm=1)
    async with _client() as c:
        await c.get("/api/auth/me")
        assert (await c.get("/api/v1/auth/me")).status_code == 429


async def test_fake_bearers_cannot_escape_ip_bucket(db):
    _enable(ip_rpm=3, token_rpm=1000)
    async with _client() as c:
        codes = [
            (await c.get("/api/v1/auth/me", headers={"Authorization": f"Bearer x{i}"})).status_code
            for i in range(5)
        ]
    assert codes[:3] == [401, 401, 401]
    assert codes[3:] == [429, 429]


async def test_valid_token_has_own_bucket(db):
    user = User(email="t@example.com", display_name="T", role="user", is_local=True, username="t")
    db.add(user)
    await db.commit()
    await db.refresh(user)
    plaintext, token_hash = generate_token()
    db.add(APIToken(user_id=user.id, name="t", token_hash=token_hash, permissions=["print"]))
    await db.commit()
    _enable(ip_rpm=100, token_rpm=2)
    headers = {"Authorization": f"Bearer {plaintext}"}
    async with _client() as c:
        assert (await c.get("/api/v1/auth/me", headers=headers)).status_code == 200
        assert (await c.get("/api/v1/auth/me", headers=headers)).status_code == 200
        assert (await c.get("/api/v1/auth/me", headers=headers)).status_code == 429
        # Another client on the same IP without the token is unaffected.
        assert (await c.get("/api/v1/auth/me")).status_code == 401


async def test_exemptions(db):
    _enable(ip_rpm=1)
    async with _client() as c:
        for path in ("/api/v1/system/health", "/eSCL/ScannerStatus", "/some/spa/route"):
            for _ in range(3):
                assert (await c.get(path)).status_code != 429, path


async def test_ingest_exempt_only_from_real_loopback_peer(db):
    _enable(ip_rpm=1)
    path = "/api/v1/jobs/internal/ingest"
    async with _client("127.0.0.1") as c:
        for _ in range(3):
            assert (await c.post(path)).status_code != 429
    # Remote peer spoofing loopback via XFF is not exempt.
    async with _client("203.0.113.9") as c:
        h = {"X-Forwarded-For": "127.0.0.1"}
        await c.post(path, headers=h)
        assert (await c.post(path, headers=h)).status_code == 429
