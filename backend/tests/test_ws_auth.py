"""F48: authenticate_websocket()/has_ws_permission() and the four WS routes
that gate on them.

Three layers:

- Unit tests drive `authenticate_websocket`/`has_ws_permission` directly
  against a fake WebSocket (headers/query_params/session), sharing the `db`
  fixture's session on the pytest-asyncio function loop -- no TestClient
  involved, so there's no event-loop boundary to worry about. This covers
  every resolution branch (Bearer, `?token=`, session cookie, Origin check,
  permission scoping).
- Integration tests go through the real routes with Starlette's
  `TestClient` (httpx's `ASGITransport` has no WebSocket support). Because
  `TestClient.websocket_connect` runs the ASGI app in its own background
  thread with its own event loop, and asyncpg connections are bound to the
  loop that created them, DB setup/teardown around a TestClient call runs
  through a throwaway `asyncio.run()` with `engine.dispose()` immediately
  after every phase -- seed, request, cleanup -- so no pooled connection
  ever crosses from one loop to another (verified empirically; skipping the
  dispose calls reproduces "RuntimeError: Event loop is closed").
- One pool-level test asserts the fix for the critical finding: the auth
  session must be closed/released *before* accept(), not held for the
  socket's whole lifetime.
"""
import asyncio
import uuid

import pytest
from sqlalchemy import delete
from starlette.datastructures import Headers, QueryParams
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.auth.tokens import hash_token
from app.auth.ws import authenticate_websocket, has_ws_permission
from app.database import async_session, engine
from app.main import app
from app.models import APIToken, User

WS_ROUTES = [
    "/api/system/ws/jobs",
    "/api/system/ws/scans",
    "/api/system/ws/printers",
    "/api/scanner/ws/scan/probe-scan-id",
]

# Each route's required permission -- mirrors the HTTP routes serving the
# same data (jobs.py/printers.py use require_permission("print"),
# scanner.py uses require_permission("scan")).
ROUTE_PERMISSIONS = {
    "/api/system/ws/jobs": "print",
    "/api/system/ws/scans": "scan",
    "/api/system/ws/printers": "print",
    "/api/scanner/ws/scan/probe-scan-id": "scan",
}


class FakeWebSocket:
    """Just enough of Starlette's WebSocket surface for authenticate_websocket:
    `.headers`, `.query_params`, `.session`."""

    def __init__(self, *, headers=None, query_params=None, session=None):
        self.headers = Headers(headers or {})
        self.query_params = QueryParams(query_params or {})
        self.session = session if session is not None else {}


async def _make_user_with_token(db, *, permissions=("print", "scan")) -> tuple[User, str]:
    user = User(
        email="wsauth@example.com", display_name="Ws Auth",
        role="user", is_local=True, username=f"wsauth_{uuid.uuid4().hex[:8]}",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    plaintext = f"pprs_test_ws_{uuid.uuid4().hex}"
    db.add(APIToken(
        user_id=user.id, name="ws-token",
        token_hash=hash_token(plaintext), permissions=list(permissions),
    ))
    await db.commit()
    return user, plaintext


# --- Unit tests: authenticate_websocket() directly ---------------------


async def test_no_credentials_returns_none(db):
    ws = FakeWebSocket()
    assert await authenticate_websocket(ws, db) is None


async def test_valid_bearer_header_returns_identity(db):
    user, plaintext = await _make_user_with_token(db, permissions=("print", "scan"))
    ws = FakeWebSocket(headers={"authorization": f"Bearer {plaintext}"})
    result = await authenticate_websocket(ws, db)
    assert result is not None
    assert result.user.id == user.id
    assert result.permissions == ["print", "scan"]


async def test_invalid_bearer_header_returns_none(db):
    ws = FakeWebSocket(headers={"authorization": "Bearer pprs_does_not_exist"})
    assert await authenticate_websocket(ws, db) is None


async def test_valid_query_token_returns_identity(db):
    user, plaintext = await _make_user_with_token(db)
    ws = FakeWebSocket(query_params={"token": plaintext})
    result = await authenticate_websocket(ws, db)
    assert result is not None
    assert result.user.id == user.id


async def test_invalid_query_token_returns_none(db):
    ws = FakeWebSocket(query_params={"token": "pprs_does_not_exist"})
    assert await authenticate_websocket(ws, db) is None


async def test_valid_session_returns_identity_with_no_permission_scoping(db):
    """Session users get `permissions=None` -- full access, mirroring how
    `require_permission` treats `token_permissions is None`."""
    user, _ = await _make_user_with_token(db)
    ws = FakeWebSocket(session={"user_id": str(user.id)})
    result = await authenticate_websocket(ws, db)
    assert result is not None
    assert result.user.id == user.id
    assert result.permissions is None


async def test_empty_session_returns_none(db):
    ws = FakeWebSocket(session={})
    assert await authenticate_websocket(ws, db) is None


async def test_session_for_deleted_user_returns_none(db):
    ws = FakeWebSocket(session={"user_id": str(uuid.uuid4())})
    assert await authenticate_websocket(ws, db) is None


async def test_bearer_takes_priority_over_session(db):
    """Mirrors get_current_user's order: Bearer wins even when a (possibly
    stale) session is also present."""
    bearer_user, plaintext = await _make_user_with_token(db)
    other_user, _ = await _make_user_with_token(db)
    ws = FakeWebSocket(
        headers={"authorization": f"Bearer {plaintext}"},
        session={"user_id": str(other_user.id)},
    )
    result = await authenticate_websocket(ws, db)
    assert result is not None
    assert result.user.id == bearer_user.id


async def test_origin_matching_host_is_allowed(db):
    user, plaintext = await _make_user_with_token(db)
    ws = FakeWebSocket(
        headers={
            "authorization": f"Bearer {plaintext}",
            "origin": "http://example.com",
            "host": "example.com",
        },
    )
    result = await authenticate_websocket(ws, db)
    assert result is not None
    assert result.user.id == user.id


async def test_origin_mismatched_host_is_rejected(db):
    user, plaintext = await _make_user_with_token(db)
    ws = FakeWebSocket(
        headers={
            "authorization": f"Bearer {plaintext}",
            "origin": "http://evil.example",
            "host": "example.com",
        },
    )
    assert await authenticate_websocket(ws, db) is None


async def test_missing_origin_is_allowed(db):
    """Non-browser clients (curl, native apps) send no Origin header at all."""
    user, plaintext = await _make_user_with_token(db, permissions=("print", "scan"))
    ws = FakeWebSocket(headers={"authorization": f"Bearer {plaintext}", "host": "example.com"})
    result = await authenticate_websocket(ws, db)
    assert result is not None
    assert result.user.id == user.id


# --- Unit tests: has_ws_permission() ------------------------------------


async def test_has_ws_permission_session_identity_passes_any_permission(db):
    user, _ = await _make_user_with_token(db)
    ws = FakeWebSocket(session={"user_id": str(user.id)})
    identity = await authenticate_websocket(ws, db)
    assert has_ws_permission(identity, "print") is True
    assert has_ws_permission(identity, "scan") is True
    assert has_ws_permission(identity, "admin") is True


async def test_has_ws_permission_token_identity_scoped_to_its_permissions(db):
    _, plaintext = await _make_user_with_token(db, permissions=("scan",))
    ws = FakeWebSocket(headers={"authorization": f"Bearer {plaintext}"})
    identity = await authenticate_websocket(ws, db)
    assert has_ws_permission(identity, "scan") is True
    assert has_ws_permission(identity, "print") is False


# --- Integration tests: the real routes via TestClient ------------------


def _run(coro):
    """Run `coro` on a throwaway loop, then dispose the engine's pool -- see
    module docstring for why this matters around every TestClient call."""
    result = asyncio.run(coro)
    asyncio.run(engine.dispose())
    return result


def _token_fixture(permissions: tuple[str, ...], *, name: str):
    """Build a pytest fixture yielding a Bearer token scoped to exactly
    `permissions`, seeded/torn down through their own throwaway event loops
    (see module docstring).

    `name=` is required: every fixture built here would otherwise share the
    inner function's own `__name__` ("_fixture") and pytest would register
    them all under that one name instead of the module-level name each is
    assigned to.
    """

    @pytest.fixture(name=name)
    def _fixture(migrated_db):
        async def _seed():
            async with async_session() as session:
                user, plaintext = await _make_user_with_token(session, permissions=permissions)
                return user.id, plaintext

        user_id, plaintext = _run(_seed())

        yield plaintext

        async def _cleanup():
            async with async_session() as session:
                await session.execute(delete(APIToken).where(APIToken.user_id == user_id))
                await session.execute(delete(User).where(User.id == user_id))
                await session.commit()

        _run(_cleanup())

    return _fixture


ws_token = _token_fixture(("print", "scan"), name="ws_token")
scan_only_token = _token_fixture(("scan",), name="scan_only_token")
print_only_token = _token_fixture(("print",), name="print_only_token")


@pytest.mark.parametrize("path", WS_ROUTES)
def test_route_rejects_unauthenticated_with_1008(path):
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(path):
            pass
    assert exc_info.value.code == 1008


@pytest.mark.parametrize("path", WS_ROUTES)
def test_route_rejects_invalid_bearer_with_1008(path, migrated_db):
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            path, headers={"Authorization": "Bearer pprs_does_not_exist"}
        ):
            pass
    assert exc_info.value.code == 1008
    asyncio.run(engine.dispose())


@pytest.mark.parametrize("path", WS_ROUTES)
def test_route_accepts_valid_bearer(path, ws_token):
    client = TestClient(app)
    # No exception -- the handshake completes and the connection stays open
    # until the `with` block exits.
    with client.websocket_connect(
        path, headers={"Authorization": f"Bearer {ws_token}"}
    ):
        pass
    asyncio.run(engine.dispose())


def test_route_rejects_mismatched_origin_with_1008(ws_token):
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            "/api/system/ws/jobs",
            headers={
                "Authorization": f"Bearer {ws_token}",
                "origin": "http://evil.example",
            },
        ):
            pass
    assert exc_info.value.code == 1008
    asyncio.run(engine.dispose())


def test_route_accepts_matching_origin(ws_token):
    client = TestClient(app)
    with client.websocket_connect(
        "/api/system/ws/jobs",
        headers={
            "Authorization": f"Bearer {ws_token}",
            "origin": "http://testserver",
        },
    ):
        pass
    asyncio.run(engine.dispose())


# --- Integration tests: per-route permission gating ---------------------


@pytest.mark.parametrize("path", ["/api/system/ws/jobs", "/api/system/ws/printers"])
def test_scan_only_token_rejected_on_print_gated_routes(path, scan_only_token):
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            path, headers={"Authorization": f"Bearer {scan_only_token}"}
        ):
            pass
    assert exc_info.value.code == 1008
    asyncio.run(engine.dispose())


@pytest.mark.parametrize(
    "path", ["/api/system/ws/scans", "/api/scanner/ws/scan/probe-scan-id"]
)
def test_scan_only_token_accepted_on_scan_gated_routes(path, scan_only_token):
    client = TestClient(app)
    with client.websocket_connect(
        path, headers={"Authorization": f"Bearer {scan_only_token}"}
    ):
        pass
    asyncio.run(engine.dispose())


@pytest.mark.parametrize(
    "path", ["/api/system/ws/scans", "/api/scanner/ws/scan/probe-scan-id"]
)
def test_print_only_token_rejected_on_scan_gated_routes(path, print_only_token):
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            path, headers={"Authorization": f"Bearer {print_only_token}"}
        ):
            pass
    assert exc_info.value.code == 1008
    asyncio.run(engine.dispose())


@pytest.mark.parametrize("path", ["/api/system/ws/jobs", "/api/system/ws/printers"])
def test_print_only_token_accepted_on_print_gated_routes(path, print_only_token):
    client = TestClient(app)
    with client.websocket_connect(
        path, headers={"Authorization": f"Bearer {print_only_token}"}
    ):
        pass
    asyncio.run(engine.dispose())


# --- Critical fix: the auth session must not be held for the socket's life --


@pytest.mark.parametrize("path", WS_ROUTES)
def test_authenticated_socket_releases_its_db_connection_before_accept(path, ws_token):
    """The auth-only session must already be closed/released by the time
    the handshake completes -- a `Depends(get_db)` session held for the
    whole request would otherwise stay checked out of the pool,
    idle-in-transaction, for as long as the socket stays open. With the
    fix, no connection is checked out while the socket sits open and idle."""
    client = TestClient(app)
    with client.websocket_connect(
        path, headers={"Authorization": f"Bearer {ws_token}"}
    ):
        assert engine.pool.checkedout() == 0
    asyncio.run(engine.dispose())
