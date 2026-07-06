"""Outgoing webhook notification service."""

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import cast, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Webhook
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


def _sign_payload(payload: bytes, secret: str) -> str:
    """Create HMAC-SHA256 signature for webhook payload."""
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


async def dispatch_webhook(
    db: AsyncSession,
    event: str,
    data: dict[str, Any],
) -> None:
    """Send webhook notifications for the given event to all matching subscribers.

    This is fire-and-forget — failures are logged but never raised.
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

    client = get_http_client()
    for webhook in matching:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "X-Papyrus-Event": event,
        }
        if webhook.secret:
            headers["X-Papyrus-Signature"] = _sign_payload(payload, webhook.secret)

        try:
            resp = await client.post(
                webhook.url,
                content=payload,
                headers=headers,
                timeout=10.0,
            )
            if resp.status_code >= 400:
                logger.warning(
                    "Webhook %s (%s) returned %d for event %s",
                    webhook.name, webhook.url, resp.status_code, event,
                )
        except Exception as exc:
            logger.warning(
                "Webhook %s (%s) failed for event %s: %s",
                webhook.name, webhook.url, event, exc,
            )
