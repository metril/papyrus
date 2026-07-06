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

KNOWN BUG surfaced by this suite (not fixed here — tests only per task
scope): ``ServerErrorMiddleware`` (which handles the bare ``Exception``
catch-all) is *outside* ``RequestIDMiddleware`` in the real middleware
stack. When a bare exception unwinds out of ``RequestIDMiddleware``'s
``await self.app(...)``, its ``finally`` block resets ``request_id_var``
to ``None`` *before* ``ServerErrorMiddleware`` calls
``_handle_unexpected_error``, and it sends the response on the raw
ASGI ``send`` that predates ``RequestIDMiddleware``'s header-injecting
``send_wrapper``. Net effect: for this one path only, ``response.json()["request_id"]``
comes back ``None`` and the ``X-Request-ID`` response header is missing
entirely — exactly the case where request-ID correlation matters most.
Domain ``PapyrusError``/``cups.IPPError`` responses are unaffected (they're
handled by the inner ``ExceptionMiddleware``, still inside
``RequestIDMiddleware``'s scope) — see
``test_domain_exception_keeps_curated_detail_at_500`` below, which passes.
The two facts this bug implies are pinned as ``xfail(strict=True)`` so the
suite stays green today and turns red (forcing someone to notice) the day
the middleware ordering is fixed and these start passing for real.
"""
import pytest
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
    # Exact body — not a substring check: exactly these two keys, curated
    # generic detail, no leaked exception text riding along.
    assert resp.json() == {"detail": "Internal server error", "request_id": None}
    assert "boom" not in resp.text


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known bug: ServerErrorMiddleware (bare-Exception handler) sits "
        "outside RequestIDMiddleware, so by the time it responds the "
        "request-ID contextvar has already been reset and the "
        "header-injecting send wrapper has already been bypassed. See the "
        "module docstring. Remove this xfail once the middleware ordering "
        "is fixed."
    ),
)
async def test_deep_runtime_error_response_should_carry_request_id(admin_client, monkeypatch):
    _patch_discover_to_raise(monkeypatch)

    resp = await admin_client.get("/api/printers/discover")

    assert resp.status_code == 500
    body = resp.json()
    assert body["request_id"]
    assert resp.headers["x-request-id"] == body["request_id"]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Same known bug as test_deep_runtime_error_response_should_carry_request_id: "
        "a custom incoming X-Request-ID is lost for the bare-Exception path "
        "specifically, since RequestIDMiddleware's context/send-wrapper "
        "never reach ServerErrorMiddleware."
    ),
)
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
