"""Pure-ASGI rate-limit middleware (HTTP only).

Every counted request spends from an IP bucket; a request carrying a Bearer
that resolves to a valid API token additionally spends from a bucket keyed by
the token's hash. Garbage bearers only hit the IP bucket, so rotating fake
tokens evades nothing. Limits come from UI-managed settings via
``settings_cache`` (30s TTL) -- no env vars.
"""
import json
import time

from starlette.types import ASGIApp, Receive, Scope, Send

from app.auth.tokens import hash_token
from app.routers.escl import _is_lan_address
from app.services import settings_cache
from app.services.rate_limit import RateLimiter, retry_after_seconds

DEFAULT_TOKEN_RPM = 600
DEFAULT_IP_RPM = 300
_VALID_TOKEN_TTL = 30.0
_VALID_TOKEN_MAX = 5000
_LOOPBACK = ("127.0.0.1", "::1")

limiter = RateLimiter()
# token hash -> expiry (monotonic) for hashes recently confirmed valid.
_valid_tokens: dict[str, float] = {}


def _exempt(path: str) -> bool:
    if not (path == "/api" or path.startswith("/api/")):
        return True  # SPA / static / docs
    return path == "/api/v1/system/health" or path == "/api/system/health"


def _is_ingest(path: str) -> bool:
    return path in ("/api/v1/jobs/internal/ingest", "/api/jobs/internal/ingest")


def _bucket_ip(scope: Scope, peer: str) -> str:
    """Client IP for the per-IP bucket.

    uvicorn only rewrites the peer from X-Forwarded-For when the proxy is on
    127.0.0.1. A reverse proxy on a docker bridge (a private peer) would
    otherwise put every user behind it into one shared bucket, so, exactly as
    the eSCL LAN check does, a private/loopback peer's *rightmost* XFF entry
    (the one that trusted hop appended) is used instead. A public peer is
    never trusted to supply XFF.
    """
    if not _is_lan_address(peer):
        return peer
    for name, value in scope.get("headers") or []:
        if name == b"x-forwarded-for":
            rightmost = value.decode("latin-1").rsplit(",", 1)[-1].strip()
            return rightmost or peer
    return peer


async def _int_setting(key: str, default: int) -> int:
    hit, val = settings_cache.get(key)
    if not hit:
        from app.database import async_session
        from app.routers.settings import get_setting

        async with async_session() as db:
            val = await get_setting(db, key)
    try:
        n = int(val) if val is not None and str(val).strip() != "" else default
    except (TypeError, ValueError):
        return default
    return n if n > 0 else default


async def _bool_setting(key: str, default: bool) -> bool:
    hit, val = settings_cache.get(key)
    if not hit:
        from app.database import async_session
        from app.routers.settings import get_setting

        async with async_session() as db:
            val = await get_setting(db, key)
    if val is None:
        return default
    return str(val).strip().lower() in ("true", "1", "yes")


async def _is_valid_token(token_hash: str, plaintext: str) -> bool:
    now = time.monotonic()
    exp = _valid_tokens.get(token_hash)
    if exp is not None and exp > now:
        return True
    from app.auth.tokens import validate_token
    from app.database import async_session

    try:
        async with async_session() as db:
            ok = await validate_token(db, plaintext) is not None
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

        ip = _bucket_ip(scope, peer)
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
