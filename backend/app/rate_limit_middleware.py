"""Pure-ASGI rate-limit middleware (HTTP only).

Every counted request spends from an IP bucket; a request carrying a Bearer
that resolves to a valid API token additionally spends from a bucket keyed by
the token's hash. Garbage bearers only hit the IP bucket, so rotating fake
tokens evades nothing. Limits come from UI-managed settings via
``settings_cache`` (30s TTL) -- no env vars.
"""
import asyncio
import ipaddress
import json
import time
from functools import lru_cache

from starlette.types import ASGIApp, Receive, Scope, Send

from app.auth.tokens import hash_token
from app.services import settings_cache
from app.services.rate_limit import RateLimiter, retry_after_seconds

DEFAULT_TOKEN_RPM = 600
DEFAULT_IP_RPM = 300
_VALID_TOKEN_TTL = 30.0
_VALID_TOKEN_MAX = 5000
_LOOPBACK = ("127.0.0.1", "::1")
DEFAULT_TRUSTED_PROXIES = "127.0.0.1/32,::1/128,172.16.0.0/12"

limiter = RateLimiter()
# token hash -> expiry (monotonic) for hashes recently confirmed valid.
_valid_tokens: dict[str, float] = {}


def _exempt(path: str) -> bool:
    if not (path == "/api" or path.startswith("/api/")):
        return True  # SPA / static / docs
    return path == "/api/v1/system/health" or path == "/api/system/health"


def _is_ingest(path: str) -> bool:
    return path in ("/api/v1/jobs/internal/ingest", "/api/jobs/internal/ingest")


@lru_cache(maxsize=16)
def parse_trusted_proxies(value: str) -> tuple:
    """Parse a comma-separated IP/CIDR list; invalid entries are skipped."""
    nets = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            continue
    return tuple(nets)


def _is_trusted_proxy(peer: str, trusted: str) -> bool:
    try:
        addr = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(addr in net for net in parse_trusted_proxies(trusted) if net.version == addr.version)


def _bucket_ip(scope: Scope, peer: str, trusted: str = DEFAULT_TRUSTED_PROXIES) -> str:
    """Client IP for the per-IP bucket.

    X-Forwarded-For is honoured only when the TCP peer is one of the
    configured ``trusted_proxies`` (so a LAN client cannot rotate a forged
    header to dodge its bucket); then the *rightmost* entry, the one the
    trusted hop appended, is the client. Any other peer is bucketed by its
    own address.
    """
    if not _is_trusted_proxy(peer, trusted):
        return peer
    for name, value in scope.get("headers") or []:
        if name == b"x-forwarded-for":
            rightmost = value.decode("latin-1").rsplit(",", 1)[-1].strip()
            return rightmost or peer
    return peer


async def _shielded(coro):
    """Run ``coro`` to completion even if the request is cancelled.

    A client disconnect cancels the request task; letting that interrupt a
    session mid-query or mid-close can leak an un-rolled-back connection.
    The inner task finishes (and closes its session) on its own.
    """
    task = asyncio.ensure_future(coro)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Retrieve the inner result/exception so it isn't reported as unretrieved.
        task.add_done_callback(lambda t: t.cancelled() or t.exception())
        raise


async def _load_setting(key: str):
    from app.database import async_session
    from app.routers.settings import get_setting

    async with async_session() as db:
        return await get_setting(db, key)


async def _raw_setting(key: str):
    hit, val = settings_cache.get(key)
    if not hit:
        val = await _shielded(_load_setting(key))
    return val


async def _int_setting(key: str, default: int) -> int:
    val = await _raw_setting(key)
    try:
        n = int(val) if val is not None and str(val).strip() != "" else default
    except (TypeError, ValueError):
        return default
    return n if n > 0 else default


async def _bool_setting(key: str, default: bool) -> bool:
    val = await _raw_setting(key)
    if val is None:
        return default
    return str(val).strip().lower() in ("true", "1", "yes")


async def _trusted_proxies() -> str:
    val = await _raw_setting("trusted_proxies")
    return DEFAULT_TRUSTED_PROXIES if val is None else str(val)


async def _check_token(plaintext: str) -> bool:
    from app.auth.tokens import validate_token
    from app.database import async_session

    async with async_session() as db:
        return await validate_token(db, plaintext) is not None


async def _is_valid_token(token_hash: str, plaintext: str) -> bool:
    now = time.monotonic()
    exp = _valid_tokens.get(token_hash)
    if exp is not None and exp > now:
        return True
    try:
        ok = await _shielded(_check_token(plaintext))
    except asyncio.CancelledError:
        raise
    except Exception:
        return False
    if ok:
        if len(_valid_tokens) >= _VALID_TOKEN_MAX:
            _valid_tokens.clear()
        _valid_tokens[token_hash] = now + _VALID_TOKEN_TTL
    return ok


class RateLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or _exempt(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        peer = client[0] if client else "unknown"
        # The ingest exemption trusts only the real TCP peer, never XFF.
        if _is_ingest(scope["path"]) and peer in _LOOPBACK:
            await self.app(scope, receive, send)
            return

        if not await _bool_setting("rate_limit_enabled", True):
            await self.app(scope, receive, send)
            return

        ip = _bucket_ip(scope, peer, await _trusted_proxies())
        wait = limiter.check(f"ip:{ip}", await _int_setting("rate_limit_ip_rpm", DEFAULT_IP_RPM))
        if not wait:
            auth = ""
            for name, value in scope.get("headers") or []:
                if name == b"authorization":
                    auth = value.decode("latin-1")
                    break
            if auth[:7].lower() == "bearer " and auth[7:]:
                plaintext = auth[7:]
                digest = hash_token(plaintext)
                if await _is_valid_token(digest, plaintext):
                    wait = limiter.check(
                        f"tok:{digest}",
                        await _int_setting("rate_limit_token_rpm", DEFAULT_TOKEN_RPM),
                    )

        if not wait:
            await self.app(scope, receive, send)
            return

        retry = retry_after_seconds(wait)
        body = json.dumps({
            "detail": "Too many requests. Try again later.",
            "code": "rate_limited",
            "request_id": scope.get("papyrus_request_id"),
        }).encode()
        await send({
            "type": "http.response.start",
            "status": 429,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"retry-after", str(retry).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": body})
