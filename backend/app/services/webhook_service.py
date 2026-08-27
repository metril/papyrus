"""Outgoing webhook notification service."""

import asyncio
import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Any, NamedTuple

from sqlalchemy import cast, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Webhook
from app.services.crypto import decrypt_value_lenient
from app.services.http_client import get_http_client

logger = logging.getLogger(__name__)

# Events that can trigger webhooks
WEBHOOK_EVENTS = [
    "print.release",
    "print.delete",
    "print.upload",
    "print.held",
    "print.test_page",
    "scan.complete",
    "scan.delete",
    "settings.update",
    "printer.supply_low",
    "printer.error",
]

# F60: dispatch_webhook fires the actual HTTP fan-out via asyncio.create_task
# so the caller (a request handler) is never blocked waiting on a slow or
# dead subscriber -- N unreachable webhooks used to cost N x the 10s
# per-request timeout, serially, inline in e.g. the job-release response.
# Scheduled tasks are kept referenced here (and discarded on completion) so
# they aren't garbage-collected mid-flight, per the standard
# asyncio.create_task caveat.
_background_tasks: set[asyncio.Task] = set()


def _schedule(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


async def wait_for_pending_dispatches() -> None:
    """Test-only helper: wait for any webhook dispatch tasks still in flight.

    dispatch_webhook returns as soon as it schedules the fan-out task, before
    any HTTP request has actually gone out (that's the point of F60) -- tests
    that assert on the delivered request need to wait for that task to
    finish first.
    """
    pending = list(_background_tasks)
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


class _Subscriber(NamedTuple):
    name: str
    url: str
    secret: str | None


def _sign_payload(payload: bytes, secret: str) -> str:
    """Create HMAC-SHA256 signature for webhook payload."""
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


async def dispatch_webhook(
    db: AsyncSession,
    event: str,
    data: dict[str, Any],
) -> None:
    """Send webhook notifications for the given event to all matching subscribers.

    This is fire-and-forget — failures are logged but never raised, and (F60)
    delivery itself happens on a background task, not inline: this coroutine
    only queries the matching subscribers and hands them off, so it returns
    to the caller (usually a request handler) without waiting on any
    outbound HTTP call.
    """
    # `Webhook.events` is a real JSON array (not a CSV/string column), so
    # membership can be pushed into SQL rather than filtered in Python. The
    # column is declared as plain `postgresql.JSON`, whose comparator has no
    # `.contains()` (that's JSONB-only); casting to JSONB at query time gets
    # the correct `@>` containment check without needing a schema migration.
    result = await db.execute(
        select(Webhook).where(
            Webhook.enabled.is_(True),
            cast(Webhook.events, JSONB).contains([event]),
        )
    )
    matching = result.scalars().all()
    if not matching:
        return

    payload = json.dumps({
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }).encode()

    # Snapshot everything the background task needs off the ORM objects now:
    # `db` (the request's AsyncSession) will likely be closed by the time a
    # background task actually runs, and Webhook.secret (F121) is encrypted
    # at rest -- decrypt it here rather than in the background task, where a
    # legacy plaintext row (decrypt_value_lenient's fallback) would still
    # need no extra I/O either way.
    subscribers = [
        _Subscriber(w.name, w.url, decrypt_value_lenient(w.secret) if w.secret else None)
        for w in matching
    ]

    _schedule(_send_all(event, payload, subscribers))


async def _send_all(event: str, payload: bytes, subscribers: list[_Subscriber]) -> None:
    """Fan out `payload` to every subscriber concurrently."""
    await asyncio.gather(
        *(_send_one(event, payload, sub) for sub in subscribers),
        return_exceptions=True,
    )


async def _send_one(event: str, payload: bytes, sub: _Subscriber) -> None:
    client = get_http_client()
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "X-Papyrus-Event": event,
    }
    if sub.secret:
        headers["X-Papyrus-Signature"] = _sign_payload(payload, sub.secret)

    try:
        resp = await client.post(
            sub.url,
            content=payload,
            headers=headers,
            timeout=10.0,
        )
        if resp.status_code >= 400:
            logger.warning(
                "Webhook %s (%s) returned %d for event %s",
                sub.name, sub.url, resp.status_code, event,
            )
    except Exception as exc:
        logger.warning(
            "Webhook %s (%s) failed for event %s: %s",
            sub.name, sub.url, event, exc,
        )
