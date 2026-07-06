"""Tests for the request-ID contextvar and the pure-ASGI middleware that
populates it (`app.request_context`, `app.middleware`).

Drives the middleware end-to-end through a minimal FastAPI app via
`httpx.AsyncClient(transport=ASGITransport(...))` — no real network socket,
no DB.
"""
import re

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.middleware import RequestIDMiddleware
from app.request_context import get_request_id, request_id_var

GENERATED_ID_RE = re.compile(r"^[0-9a-f]{16}$")


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)

    @app.get("/ping")
    async def ping():
        return {"request_id": get_request_id()}

    return app


@pytest.fixture
def client():
    transport = ASGITransport(app=_build_app())
    return AsyncClient(transport=transport, base_url="http://test")


def test_get_request_id_defaults_to_none_outside_a_request():
    assert get_request_id() is None


async def test_generates_a_request_id_when_header_absent(client):
    async with client as c:
        resp = await c.get("/ping")

    assert resp.status_code == 200
    request_id = resp.headers["x-request-id"]
    assert GENERATED_ID_RE.match(request_id)
    # The handler saw the same ID via get_request_id().
    assert resp.json()["request_id"] == request_id


async def test_echoes_a_valid_incoming_request_id(client):
    async with client as c:
        resp = await c.get("/ping", headers={"X-Request-ID": "abc-123-DEF"})

    assert resp.headers["x-request-id"] == "abc-123-DEF"
    assert resp.json()["request_id"] == "abc-123-DEF"


async def test_replaces_an_overlong_incoming_request_id(client):
    too_long = "a" * 65
    async with client as c:
        resp = await c.get("/ping", headers={"X-Request-ID": too_long})

    returned = resp.headers["x-request-id"]
    assert returned != too_long
    assert GENERATED_ID_RE.match(returned)


async def test_replaces_an_incoming_request_id_with_invalid_characters(client):
    junky = "not valid! id/with/slashes"
    async with client as c:
        resp = await c.get("/ping", headers={"X-Request-ID": junky})

    returned = resp.headers["x-request-id"]
    assert returned != junky
    assert GENERATED_ID_RE.match(returned)


async def test_accepts_a_64_char_incoming_request_id_unchanged(client):
    exactly_64 = "b" * 64
    async with client as c:
        resp = await c.get("/ping", headers={"X-Request-ID": exactly_64})

    assert resp.headers["x-request-id"] == exactly_64


async def test_contextvar_is_reset_after_the_response_completes(client):
    assert request_id_var.get() is None
    async with client as c:
        await c.get("/ping")
    assert request_id_var.get() is None


async def test_contextvar_is_reset_even_when_the_handler_raises():
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)

    @app.get("/boom")
    async def boom():
        raise RuntimeError("kaboom")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    client = AsyncClient(transport=transport, base_url="http://test")

    assert request_id_var.get() is None
    async with client as c:
        resp = await c.get("/boom")
    assert resp.status_code == 500
    assert request_id_var.get() is None


async def test_non_http_scopes_pass_through_untouched():
    """Lifespan/websocket scopes must not be handled as HTTP (no header
    manipulation, no contextvar side effects)."""
    from app.middleware import RequestIDMiddleware

    calls = []

    async def inner_app(scope, receive, send):
        calls.append(scope["type"])

    middleware = RequestIDMiddleware(inner_app)
    await middleware({"type": "lifespan"}, None, None)

    assert calls == ["lifespan"]
    assert request_id_var.get() is None
