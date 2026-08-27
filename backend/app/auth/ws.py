import uuid
from urllib.parse import urlparse

from fastapi import WebSocket
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.tokens import validate_token
from app.models import User


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


async def authenticate_websocket(ws: WebSocket, db: AsyncSession) -> User | None:
    """Resolve the identity of an incoming WebSocket handshake, or None.

    Mirrors `app.auth.dependencies.get_current_user`'s resolution order
    exactly -- Bearer token, then session cookie -- plus a `?token=` query
    parameter as an equivalent to the Bearer header, since a browser
    WebSocket client can't set custom request headers on the handshake.
    `PAPYRUS_DEV_MODE` needs no separate branch here: dev mode's auto-login
    (`routers/auth.py`'s `/login`) works by populating the session cookie,
    which the session-cookie path below already honors.

    Never raises. Callers must treat None as "reject": close with code 1008
    before returning, and always authenticate before `ws_manager.connect()`
    (which calls `accept()`).
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
        return result.scalar_one_or_none()

    user_id = ws.session.get("user_id")
    if user_id:
        result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
        return result.scalar_one_or_none()

    return None
