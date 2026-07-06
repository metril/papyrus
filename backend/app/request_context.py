"""Per-request context propagated via a ContextVar.

Holds the current request's ID so that any code running within the request
(including log filters that have no direct access to the request object) can
retrieve it. Populated by the request-ID middleware in ``app.middleware``.
"""
import contextvars

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)


def get_request_id() -> str | None:
    """Return the current request's ID, or None outside a request."""
    return request_id_var.get()
