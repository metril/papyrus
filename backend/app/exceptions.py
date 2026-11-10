"""Domain exception hierarchy and global exception handlers.

Every ``PapyrusError`` carries a ``detail`` string that is sent verbatim to
the client, so those messages MUST be written for end users — never leak
internal state, stack traces, or upstream error strings into them. Anything
that isn't a ``PapyrusError`` (a bare ``Exception``) is caught by the
catch-all handler, which logs the traceback and returns a generic message so
internal detail never reaches the client.
"""
import logging

from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.request_context import get_request_id, request_id_var

logger = logging.getLogger(__name__)


class PapyrusError(Exception):
    """Base for domain errors whose ``detail`` is safe to show users.

    ``detail`` is client-visible curated text — write it for end users and
    never put internal/stack/upstream detail into it.
    """

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


def user_message(exc: BaseException, fallback: str = "Scan failed") -> str:
    """Text safe to store or show: a domain error's detail, else a fixed message."""
    return exc.detail if isinstance(exc, PapyrusError) else fallback


class NotFoundError(PapyrusError):
    status_code = 404
    code = "not_found"


class PrinterUnavailableError(PapyrusError):
    status_code = 503
    code = "printer_unavailable"


class ScannerBusyError(PapyrusError):
    status_code = 503
    code = "scanner_busy"


class ExternalServiceError(PapyrusError):
    status_code = 502
    code = "external_service_error"


class ScanSourceError(PapyrusError):
    """The requested scan source conflicts with the scanner's physical state."""

    status_code = 409
    code = "scan_source_conflict"


class ScanRequestError(PapyrusError):
    """The scan parameters (resolution, area, mode) are not valid."""

    status_code = 422
    code = "scan_request_invalid"


class BacksNotEligibleError(PapyrusError):
    """The scan can't take a second (back-side) pass."""

    status_code = 400
    code = "backs_not_eligible"


class BacksMismatchError(PapyrusError):
    """The back-side scan couldn't be combined with the fronts; both were kept."""

    status_code = 409
    code = "backs_mismatch"


class UploadTooLargeError(PapyrusError):
    """Raised when an upload exceeds its configured size cap."""

    status_code = 413
    code = "upload_too_large"


class TooManyAttemptsError(PapyrusError):
    """Raised when a throttle (login, release PIN, ...) is tripped."""

    status_code = 429
    code = "too_many_attempts"


def _request_id(request: Request) -> str | None:
    """Request id from the contextvar, falling back to the scope stash.

    The fallback matters for the ``Exception`` catch-all: Starlette runs it in
    ``ServerErrorMiddleware``, outside ``RequestIDMiddleware``, after the
    contextvar has been reset.
    """
    return get_request_id() or request.scope.get("papyrus_request_id")


_STATUS_PHRASES = {
    400: "Bad request",
    401: "Not authenticated",
    403: "Forbidden",
    404: "Not found",
    405: "Method not allowed",
    409: "Conflict",
    413: "Payload too large",
    422: "Request validation failed",
    429: "Too many requests",
}


def _http_code(status_code: int) -> str:
    return {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        405: "method_not_allowed",
        409: "conflict",
        413: "upload_too_large",
        422: "validation_error",
        429: "rate_limited",
    }.get(status_code, "internal_error" if status_code >= 500 else "http_error")


def register_exception_handlers(app) -> None:
    """Register the global exception handlers on ``app``.

    Factored out of ``main.py`` so tests can attach the same handlers to a
    throwaway FastAPI app without importing the whole application.
    """

    @app.exception_handler(PapyrusError)
    async def _handle_papyrus_error(request: Request, exc: PapyrusError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": exc.detail,
                "code": exc.code,
                "request_id": _request_id(request),
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else _STATUS_PHRASES.get(
            exc.status_code, "Request failed"
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": detail,
                "code": _http_code(exc.status_code),
                "request_id": _request_id(request),
            },
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Only loc/msg/type: `input` and `ctx` can echo submitted values.
        errors = [
            {
                "loc": list(e.get("loc", ())),
                "msg": str(e.get("msg", "")),
                "type": str(e.get("type", "")),
            }
            for e in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "detail": "Request validation failed",
                "code": "validation_error",
                "request_id": _request_id(request),
                "errors": errors,
            },
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        request_id = _request_id(request)
        # Re-establish the contextvar before logging: this handler runs in
        # ServerErrorMiddleware, outside RequestIDMiddleware, after the var was
        # reset — without this the traceback below would log request_id="-"
        # and the one log line an operator greps for couldn't be correlated.
        request_id_var.set(request_id)
        logger.exception("Unhandled exception processing request")
        # This response bypasses RequestIDMiddleware's send wrapper (it is
        # produced outside it), so the header must be set here.
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Internal server error",
                "code": "internal_error",
                "request_id": request_id,
            },
            headers={"X-Request-ID": request_id} if request_id else None,
        )

    # cups.IPPError → 503. Register only when `cups` is a real module exposing a
    # real exception class: under tests the conftest stubs `cups` as a
    # MagicMock, whose `.IPPError` attribute is a Mock rather than a type, and
    # FastAPI rejects non-exception handler keys.
    try:
        import cups
    except ImportError:
        cups = None
    if cups is not None:
        ipp_error = getattr(cups, "IPPError", None)
        if isinstance(ipp_error, type) and issubclass(ipp_error, BaseException):
            @app.exception_handler(ipp_error)
            async def _handle_ipp_error(request: Request, exc) -> JSONResponse:
                return JSONResponse(
                    status_code=503,
                    content={
                        "detail": "The print system did not respond. "
                        "Check the printer connection.",
                        "code": "printer_unavailable",
                        "request_id": get_request_id(),
                    },
                )
