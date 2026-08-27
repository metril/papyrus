from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_admin
from app.database import get_db
from app.models import User, Webhook
from app.schemas import WebhookCreate, WebhookResponse, WebhookUpdate
from app.services.crypto import encrypt_value
from app.services.webhook_service import WEBHOOK_EVENTS

router = APIRouter()


@router.get("/events")
async def list_webhook_events(_user: User = Depends(require_admin)) -> list[str]:
    """List all available webhook event types."""
    return WEBHOOK_EVENTS


@router.get("", response_model=list[WebhookResponse])
async def list_webhooks(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_admin),
):
    """List all configured webhooks."""
    result = await db.execute(select(Webhook).order_by(Webhook.created_at.desc()))
    return result.scalars().all()


@router.post("", response_model=WebhookResponse, status_code=201)
async def create_webhook(
    body: WebhookCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Create a new webhook."""
    # Validate events
    invalid = [e for e in body.events if e not in WEBHOOK_EVENTS]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Invalid events: {invalid}")

    webhook = Webhook(
        name=body.name,
        url=body.url,
        # F121: encrypted at rest, like every other stored credential/secret
        # in the schema. Read side (webhook_service.dispatch_webhook) uses
        # decrypt_value_lenient so a secret written before this fix
        # (plaintext) still verifies until it's next saved through here.
        secret=encrypt_value(body.secret) if body.secret else None,
        events=body.events,
        enabled=body.enabled,
        created_by=user.id,
    )
    db.add(webhook)
    await db.commit()
    await db.refresh(webhook)
    return webhook


@router.put("/{webhook_id}", response_model=WebhookResponse)
async def update_webhook(
    webhook_id: int,
    body: WebhookUpdate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_admin),
):
    """Update a webhook.

    PATCH semantics (F18): a field the client didn't send is left alone, so
    e.g. the enable/disable toggle can PUT just `{"enabled": ...}` without
    clobbering `secret` — `WebhookResponse` never returns it, so the client
    has no way to echo it back, and unconditionally overwriting it (the
    previous behaviour) silently nulled the HMAC signing secret on every
    toggle, after which deliveries went out unsigned.
    """
    webhook = await db.get(Webhook, webhook_id)
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook not found")

    updates = body.model_dump(exclude_unset=True)

    if "events" in updates:
        invalid = [e for e in body.events if e not in WEBHOOK_EVENTS]
        if invalid:
            raise HTTPException(status_code=400, detail=f"Invalid events: {invalid}")
        webhook.events = body.events

    if "name" in updates:
        webhook.name = body.name
    if "url" in updates:
        webhook.url = body.url
    if "enabled" in updates:
        webhook.enabled = body.enabled
    # secret: only a non-null value changes anything -- omitted *or*
    # explicitly null both keep the stored secret (F18/F121).
    if "secret" in updates and body.secret is not None:
        webhook.secret = encrypt_value(body.secret) if body.secret else None

    await db.commit()
    await db.refresh(webhook)
    return webhook


@router.delete("/{webhook_id}", status_code=204)
async def delete_webhook(
    webhook_id: int,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_admin),
):
    """Delete a webhook."""
    webhook = await db.get(Webhook, webhook_id)
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook not found")
    await db.delete(webhook)
    await db.commit()
