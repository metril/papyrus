from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_admin
from app.database import get_db
from app.models import AppConfig, User
from app.schemas import EmailConfig, EmailConfigStatus
from app.services.crypto import encrypt_value
from app.services.email_service import email_service

router = APIRouter()


async def _get_smtp_config(db: AsyncSession) -> dict:
    """Load SMTP config from database."""
    result = await db.execute(
        select(AppConfig).where(AppConfig.key.like("smtp_%"))
    )
    rows = result.scalars().all()
    return {row.key: row.value for row in rows}


@router.get("/config", response_model=EmailConfigStatus)
async def get_email_config(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get SMTP configuration status (no secrets returned)."""
    db_config = await _get_smtp_config(db)
    return EmailConfigStatus(
        configured=email_service.is_configured(db_config),
        smtp_host=db_config.get("smtp_host"),
        smtp_from=db_config.get("smtp_from"),
    )


@router.put("/config")
async def update_email_config(
    data: EmailConfig,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update SMTP configuration (admin only)."""
    config_items = {
        "smtp_host": data.smtp_host,
        "smtp_port": str(data.smtp_port),
        "smtp_user": data.smtp_user,
        "smtp_password_encrypted": encrypt_value(data.smtp_password),
        "smtp_from": data.smtp_from,
    }

    for key, value in config_items.items():
        result = await db.execute(select(AppConfig).where(AppConfig.key == key))
        existing = result.scalar_one_or_none()
        if existing:
            existing.value = value
        else:
            db.add(AppConfig(key=key, value=value))

    await db.commit()
    return {"message": "SMTP configuration updated"}


@router.post("/test")
async def test_email(
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Test SMTP connection."""
    db_config = await _get_smtp_config(db)
    success = await email_service.test_connection(db_config)
    if not success:
        raise HTTPException(status_code=502, detail="SMTP connection failed")
    return {"message": "SMTP connection successful"}
