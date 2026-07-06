"""Error-handler integration through the real, fully-assembled app.

Complements two existing unit suites, neither of which exercises the real
app: test_exception_handlers.py drives ``register_exception_handlers`` on a
throwaway FastAPI app with no middleware, and test_request_context.py drives
``RequestIDMiddleware`` on a minimal app with no exception handlers. This
suite goes through ``app.main.app`` end-to-end (session/CORS middleware,
``RequestIDMiddleware``, and the registered handlers all wired together, via
the same ``client``/``admin_client`` ASGI fixtures the other API suites use)
to prove the request ID set by the middleware is the same one the exception
handlers read back via ``get_request_id()``, and that a genuinely deep bug
in a real route is sanitized before it reaches the client.

A curated-detail domain-exception path through the real app is already
covered by test_api_printers.py's ``test_test_page_failure_is_502_with_request_id``
(an ``ExternalServiceError``/502). This suite adds a distinct one — the base
``PapyrusError`` (default status_code 500) raised by ``POST
/api/printers/{id}/resume`` — specifically to contrast against the bare
``RuntimeError`` case below: both come back as HTTP 500, but only the domain
exception's curated detail should reach the client.

The shared ``client``/``admin_client`` fixtures (from conftest.py) build
their ``ASGITransport`` with the httpx default ``raise_app_exceptions=True``.
That's invisible for every other suite because a registered ``PapyrusError``
handler runs inside Starlette's inner ``ExceptionMiddleware``, which never
re-raises. The bare-``Exception`` catch-all handler is different: Starlette
wires it into the *outer* ``ServerErrorMiddleware``, which always re-raises
the original exception after sending the response (so an ASGI server/logger
still sees it) — and ``raise_app_exceptions=True`` propagates that re-raise
straight into the httpx call, surfacing the raw ``RuntimeError`` in the test
instead of the JSON 500 the handler actually sent. This module overrides
``client`` with ``raise_app_exceptions=False`` so the real response comes
back for inspection; pytest resolves fixtures by nearest scope, so
conftest's ``admin_client`` (which merely depends on a fixture named
``client``) transparently picks up this module's version for every test
here.

Regression context: ``ServerErrorMiddleware`` (which handles the bare
``Exception`` catch-all) is *outside* ``RequestIDMiddleware``, so the
contextvar is already reset and the header-injecting ``send_wrapper`` is
bypassed by the time the catch-all responds. The fix: ``RequestIDMiddleware``
stashes the id on the ASGI scope (``papyrus_request_id``), the handlers fall
back to it via ``exceptions._request_id``, and the catch-all sets its own
``X-Request-ID`` header. The request-id tests below are the regression tests
for that path.
"""
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models import Printer
from app.routers import printers as printers_router


@pytest_asyncio.fixture(loop_scope="function")
async def client(db):
    """Overrides conftest's ``client`` for this module only — see the module
    docstring for why ``raise_app_exceptions=False`` is required here."""
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def _seed_printer(db) -> Printer:
    printer = Printer(
        display_name="Brother", cups_name="brother", uri="ipp://x/ipp", is_network_queue=False
    )
    db.add(printer)
    await db.commit()
    await db.refresh(printer)
    return printer


def _patch_discover_to_raise(monkeypatch, message: str = "boom") -> None:
    async def _boom(timeout: float = 4.0):
        raise RuntimeError(message)

    monkeypatch.setattr(printers_router, "discover_printers", _boom)


# --------------------------------------------------------------------------- #
# Bare Exception -> sanitized 500
# --------------------------------------------------------------------------- #
async def test_deep_runtime_error_is_sanitized_500(admin_client, monkeypatch):
    _patch_discover_to_raise(monkeypatch, "boom: leaking internal stack detail")

    resp = await admin_client.get("/api/printers/discover")

    assert resp.status_code == 500
    body = resp.json()
    # Exactly these two keys: curated generic detail plus the correlation id,
    # no leaked exception text riding along.
    assert set(body) == {"detail", "request_id"}
    assert body["detail"] == "Internal server error"
    assert "boom" not in resp.text


async def test_deep_runtime_error_response_should_carry_request_id(admin_client, monkeypatch):
    _patch_discover_to_raise(monkeypatch)

    resp = await admin_client.get("/api/printers/discover")

    assert resp.status_code == 500
    body = resp.json()
    assert body["request_id"]
    assert resp.headers["x-request-id"] == body["request_id"]


async def test_custom_request_id_is_echoed_into_error_body(admin_client, monkeypatch):
    _patch_discover_to_raise(monkeypatch)

    resp = await admin_client.get(
        "/api/printers/discover", headers={"X-Request-ID": "myid123"}
    )

    assert resp.status_code == 500
    assert resp.headers["x-request-id"] == "myid123"
    assert resp.json()["request_id"] == "myid123"


# --------------------------------------------------------------------------- #
# Domain exception -> curated detail (contrast against the bare Exception above)
# --------------------------------------------------------------------------- #
async def test_domain_exception_keeps_curated_detail_at_500(db, admin_client, monkeypatch):
    printer = await _seed_printer(db)

    async def _fake_enable_queue(name: str) -> None:
        raise RuntimeError("cupsenable 'brother' failed: no such printer-or-class")

    monkeypatch.setattr(printers_router.cups_admin, "enable_queue", _fake_enable_queue)

    resp = await admin_client.post(f"/api/printers/{printer.id}/resume")

    assert resp.status_code == 500
    body = resp.json()
    assert body["detail"] == "Re-enabling the printer queue failed."
    assert "no such printer-or-class" not in resp.text  # raw cupsenable stderr never leaks
    assert body["request_id"]
    assert resp.headers["x-request-id"] == body["request_id"]


async def test_domain_exception_echoes_custom_request_id(db, admin_client, monkeypatch):
    """Unlike the bare-Exception path above, a domain ``PapyrusError`` is
    handled by the inner ``ExceptionMiddleware`` — still inside
    ``RequestIDMiddleware``'s scope — so a caller-supplied X-Request-ID
    really is echoed into both the header and the body."""
    printer = await _seed_printer(db)

    async def _fake_enable_queue(name: str) -> None:
        raise RuntimeError("cupsenable failed")

    monkeypatch.setattr(printers_router.cups_admin, "enable_queue", _fake_enable_queue)

    resp = await admin_client.post(
        f"/api/printers/{printer.id}/resume", headers={"X-Request-ID": "myid123"}
    )

    assert resp.status_code == 500
    assert resp.headers["x-request-id"] == "myid123"
    assert resp.json()["request_id"] == "myid123"


async def test_unhandled_error_traceback_log_carries_request_id(admin_client, monkeypatch):
    """Regression: the catch-all handler re-establishes the request-id
    contextvar before logging (it runs in ServerErrorMiddleware, after
    RequestIDMiddleware reset the var), so the traceback log line carries the
    same id the client received — not "-"."""
    import logging

    from app.logging_config import RequestIdFilter

    _patch_discover_to_raise(monkeypatch)

    records: list[logging.LogRecord] = []
    capture = logging.Handler()
    capture.emit = records.append  # runs after handler-level filters
    capture.addFilter(RequestIdFilter())
    exc_logger = logging.getLogger("app.exceptions")
    exc_logger.addHandler(capture)
    try:
        resp = await admin_client.get(
            "/api/printers/discover", headers={"X-Request-ID": "corr-123"}
        )
    finally:
        exc_logger.removeHandler(capture)

    assert resp.status_code == 500
    tracebacks = [r for r in records if r.exc_info]
    assert tracebacks, "expected the catch-all to log the traceback"
    assert all(r.request_id == "corr-123" for r in tracebacks)
