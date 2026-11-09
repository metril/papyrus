import uuid
from dataclasses import dataclass
from urllib.parse import urlparse

from fastapi import WebSocket
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.tokens import validate_token
from app.models import User


@dataclass
class WebSocketIdentity:
    """The resolved identity of an authenticated WS handshake.

    `permissions` mirrors `request.state.token_permissions` from the HTTP
    auth path (`app/auth/dependencies.py`): `None` means a session user --
    full access, same as `require_permission` treats `token_permissions is
    None` -- and a list means an API-token user, scoped to exactly those
    permissions. See `has_ws_permission` below.
    """

    user: User
    permissions: list[str] | None


def _origin_allowed(ws: WebSocket) -> bool:
    """Reject a cross-origin WebSocket handshake.

    WebSocket connections aren't subject to CORS, so any page on any origin
    can open one directly against this host. When the browser sends an
    `Origin` header, its host must match the `Host` header the request
    arrived on. Non-browser clients (curl, native apps, server-to-server)
    send no `Origin` header at all and are allowed through -- Origin is a
    browser-only signal, not a substitute for authentication.
    """
    origin = ws.headers.get("origin")
    if origin is None:
        return True
    return urlparse(origin).netloc == ws.headers.get("host", "")


async def authenticate_websocket(ws: WebSocket, db: AsyncSession) -> WebSocketIdentity | None:
    """Resolve the identity of an incoming WebSocket handshake, or None.

    Mirrors `app.auth.dependencies.get_current_user`'s resolution order
    exactly -- Bearer token, then session cookie -- plus a `?token=` query
    parameter as an equivalent to the Bearer header, since a browser
    WebSocket client can't set custom request headers on the handshake.
    `PAPYRUS_DEV_MODE` needs no separate branch here: dev mode's auto-login
    (`routers/auth.py`'s `/login`) works by populating the session cookie,
    which the session-cookie path below already honors.

    `db` is expected to be a short-lived session the caller opens and closes
    around just this call (e.g. `async with async_session() as db:`), never
    a request-scoped `Depends(get_db)` session held for the socket's whole
    lifetime -- see the callers in routers/system.py and routers/scanner.py
    for why that matters.

    Never raises. Callers must treat None as "reject": close with code 1008
    before returning, and always authenticate (and permission-check, via
    `has_ws_permission`) before `ws_manager.connect()` (which calls
    `accept()`).
    """
    if not _origin_allowed(ws):
        return None

    auth_header = ws.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        plaintext = auth_header[7:]
    else:
        plaintext = ws.query_params.get("token")

    if plaintext:
        token = await validate_token(db, plaintext)
        if token is None:
            return None
        result = await db.execute(select(User).where(User.id == token.user_id))
        user = result.scalar_one_or_none()
        if user is None:
            return None
        return WebSocketIdentity(user=user, permissions=token.permissions)

    user_id = ws.session.get("user_id")
    if user_id:
        try:
            session_uuid = uuid.UUID(user_id)
        except (ValueError, TypeError, AttributeError):
            return None
        result = await db.execute(select(User).where(User.id == session_uuid))
        user = result.scalar_one_or_none()
        if user is None:
            return None
        return WebSocketIdentity(user=user, permissions=None)

    return None


def has_ws_permission(identity: WebSocketIdentity, permission: str) -> bool:
    """True if `identity` may use a channel gated on `permission`.

    Mirrors `app.auth.dependencies.require_permission`: a session user
    (`permissions is None`) always passes; an API-token user must have the
    permission in their token's scoped permission list.
    """
    return identity.permissions is None or permission in identity.permissions
