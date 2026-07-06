"""Domain exception hierarchy and global exception handlers.

Every ``PapyrusError`` carries a ``detail`` string that is sent verbatim to
the client, so those messages MUST be written for end users — never leak
internal state, stack traces, or upstream error strings into them. Anything
that isn't a ``PapyrusError`` (a bare ``Exception``) is caught by the
catch-all handler, which logs the traceback and returns a generic message so
internal detail never reaches the client.
"""
import logging

from starlette.requests import Request
from starlette.responses import JSONResponse

from app.request_context import get_request_id

logger = logging.getLogger(__name__)


class PapyrusError(Exception):
    """Base for domain errors whose ``detail`` is safe to show users.

    ``detail`` is client-visible curated text — write it for end users and
    never put internal/stack/upstream detail into it.
    """

    status_code: int = 500

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


class NotFoundError(PapyrusError):
    status_code = 404


class PrinterUnavailableError(PapyrusError):
    status_code = 503


class ScannerBusyError(PapyrusError):
    status_code = 503


class ExternalServiceError(PapyrusError):
    status_code = 502


class UploadTooLargeError(PapyrusError):
    """Raised when an upload exceeds its configured size cap."""

    status_code = 413


def register_exception_handlers(app) -> None:
    """Register the global exception handlers on ``app``.

    Factored out of ``main.py`` so tests can attach the same handlers to a
    throwaway FastAPI app without importing the whole application.
    """

    @app.exception_handler(PapyrusError)
    async def _handle_papyrus_error(request: Request, exc: PapyrusError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail, "request_id": get_request_id()},
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        # logger.exception records the traceback; the Task 1 RequestIdFilter
        # stamps the request_id onto the log record.
        logger.exception("Unhandled exception processing request")
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "request_id": get_request_id()},
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
                        "request_id": get_request_id(),
                    },
                )
