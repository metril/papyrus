"""Pure-ASGI middleware for request-ID propagation.

Implemented as a raw ASGI middleware (not ``BaseHTTPMiddleware``) so it can
wrap the response stream directly and always run its cleanup, even when a
downstream handler raises.
"""
import re
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.request_context import request_id_var

_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")


def _resolve_request_id(headers: list[tuple[bytes, bytes]]) -> str:
    """Return a sanitized request ID from the incoming headers, or a
    freshly generated one if absent/invalid."""
    for name, value in headers:
        if name == b"x-request-id":
            try:
                candidate = value.decode("latin-1")
            except UnicodeDecodeError:
                break
            if _VALID_REQUEST_ID.match(candidate):
                return candidate
            break
    return uuid.uuid4().hex[:16]


class RequestIDMiddleware:
    """Assigns a request ID to every HTTP request.

    - Uses the incoming ``X-Request-ID`` header if present and well-formed
      (<=64 chars, alphanumeric/dashes only); otherwise generates one.
    - Stores it in ``request_id_var`` for the duration of the request so
      logging (and any other code) can retrieve it via ``get_request_id()``.
    - Echoes it back to the client as an ``X-Request-ID`` response header.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _resolve_request_id(scope.get("headers") or [])
        token = request_id_var.set(request_id)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                headers.append((b"x-request-id", request_id.encode("latin-1")))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            request_id_var.reset(token)
