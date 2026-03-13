"""Outgoing webhook notification service."""

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Webhook

logger = logging.getLogger(__name__)

# Events that can trigger webhooks
WEBHOOK_EVENTS = [
    "print.release",
    "print.delete",
    "print.upload",
    "scan.complete",
    "scan.delete",
    "settings.update",
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
    result = await db.execute(
        select(Webhook).where(Webhook.enabled == True)
    )
    webhooks = result.scalars().all()

    matching = [w for w in webhooks if event in (w.events or [])]
    if not matching:
        return

    payload = json.dumps({
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }).encode()

    async with httpx.AsyncClient(timeout=10.0) as client:
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
