"""Tests for the global exception handlers registered by
``app.exceptions.register_exception_handlers``.

These build a throwaway FastAPI app (no DB, no real routers) whose routes
raise each domain exception plus a bare ``Exception``, then assert the JSON
response shape. ``raise_server_exceptions=False`` is required so TestClient
returns the catch-all's 500 response instead of re-raising it.
"""
import logging

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.exceptions import (
    ExternalServiceError,
    NotFoundError,
    PapyrusError,
    PrinterUnavailableError,
    ScannerBusyError,
    UploadTooLargeError,
    register_exception_handlers,
)


def _build_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/papyrus")
    async def _papyrus():
        raise PapyrusError("base failure")

    @app.get("/notfound")
    async def _notfound():
        raise NotFoundError("no such thing")

    @app.get("/printer")
    async def _printer():
        raise PrinterUnavailableError("printer offline")

    @app.get("/scanner")
    async def _scanner():
        raise ScannerBusyError("scanner busy")

    @app.get("/external")
    async def _external():
        raise ExternalServiceError("upstream failed")

    @app.get("/toobig")
    async def _toobig():
        raise UploadTooLargeError("upload exceeds the limit")

    @app.get("/boom")
    async def _boom():
        raise RuntimeError("secret internal detail")

    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_build_app(), raise_server_exceptions=False)


@pytest.mark.parametrize(
    ("path", "status", "detail"),
    [
        ("/papyrus", 500, "base failure"),
        ("/notfound", 404, "no such thing"),
        ("/printer", 503, "printer offline"),
        ("/scanner", 503, "scanner busy"),
        ("/external", 502, "upstream failed"),
        ("/toobig", 413, "upload exceeds the limit"),
    ],
)
def test_domain_exception_maps_to_status_and_detail(client, path, status, detail):
    resp = client.get(path)
    assert resp.status_code == status
    body = resp.json()
    assert body["detail"] == detail
    # request_id key is always present (None here — no request-ID middleware).
    assert "request_id" in body


def test_upload_too_large_yields_413(client):
    assert client.get("/toobig").status_code == 413


def test_bare_exception_returns_generic_500_without_leaking(client, caplog):
    with caplog.at_level(logging.ERROR):
        resp = client.get("/boom")

    assert resp.status_code == 500
    body = resp.json()
    assert body["detail"] == "Internal server error"
    assert "request_id" in body
    # The raw exception text must never reach the client.
    assert "secret internal detail" not in resp.text
    # ...but it must be logged with a traceback for operators.
    assert any(record.exc_info for record in caplog.records)


def test_validation_error_envelope_strips_input_and_ctx():
    from pydantic import BaseModel, Field

    class Body(BaseModel):
        n: int = Field(ge=5)

    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/v")
    async def _v(body: Body):
        return {}

    r = TestClient(app).post("/v", json={"n": 1, "secret": "x"})
    body = r.json()
    assert r.status_code == 422
    assert body["detail"] == "Request validation failed"
    assert body["code"] == "validation_error"
    assert body["errors"] and all(set(e) == {"loc", "msg", "type"} for e in body["errors"])


def test_http_exception_envelope_preserves_headers():
    from fastapi import HTTPException

    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/h")
    async def _h():
        raise HTTPException(401, "nope", headers={"WWW-Authenticate": "Bearer"})

    r = TestClient(app).get("/h")
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"
    assert r.json()["detail"] == "nope" and r.json()["code"] == "unauthorized"
