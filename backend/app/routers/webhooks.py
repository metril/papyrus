from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_admin
from app.database import get_db
from app.models import User, Webhook
from app.schemas import WebhookCreate, WebhookResponse
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
        secret=body.secret,
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
    body: WebhookCreate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_admin),
):
    """Update a webhook."""
    webhook = await db.get(Webhook, webhook_id)
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook not found")

    invalid = [e for e in body.events if e not in WEBHOOK_EVENTS]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Invalid events: {invalid}")

    webhook.name = body.name
    webhook.url = body.url
    webhook.secret = body.secret
    webhook.events = body.events
    webhook.enabled = body.enabled
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
