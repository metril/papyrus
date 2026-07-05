"""Shared, lazily-created ``httpx.AsyncClient`` for outbound HTTP calls.

``webhook_service``, ``cloud_service``, ``webdav_service``, and
``paperless_service`` each used to open a brand new ``httpx.AsyncClient()``
(and therefore a fresh TCP/TLS connection) for every outbound call. Under a
single-worker deployment those calls are frequent enough (webhook dispatch,
cloud OAuth refresh + list/download/upload, WebDAV browse/download/upload,
Paperless-ngx push) that reusing one pooled client avoids repeated handshake
overhead.

Per-call differences (timeout, ``auth``, ``follow_redirects``) are passed at
the *request* level by callers — httpx supports overriding those per request
against a shared client. ``verify`` and ``base_url`` cannot be overridden per
request, so any call site that needs a non-default value for those must keep
its own dedicated client rather than adopt this shared one (none of the four
services currently do).

Single-worker/in-process only, per the project's caching conventions — no
cross-process coordination is needed or provided.
"""

import httpx

DEFAULT_TIMEOUT = 30.0

_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    """Return the shared client, creating it lazily on first use."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)
    return _client


async def close_http_client() -> None:
    """Close the shared client (if created) and reset it so a later
    ``get_http_client()`` call builds a fresh one. Call from app shutdown."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
