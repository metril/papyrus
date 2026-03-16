"""Admin endpoints: audit log, usage stats, backup/restore, retention, user management."""

import uuid as uuid_mod
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, cast, Date
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_admin
from app.database import get_db
from app.models import AuditEntry, AppConfig, PrintJob, ScanJob, User

router = APIRouter()


# --- Audit Log ---


@router.get("/audit")
async def get_audit_log(
    action: str | None = None,
    entity_type: str | None = None,
    limit: int = Query(default=50, le=200),
    offset: int = 0,
    _user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get paginated audit log with optional filters."""
    query = select(AuditEntry)

    if action:
        query = query.where(AuditEntry.action == action)
    if entity_type:
        query = query.where(AuditEntry.entity_type == entity_type)

    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    query = query.order_by(AuditEntry.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(query)
    entries = result.scalars().all()

    return {
        "entries": [
            {
                "id": e.id,
                "action": e.action,
                "entity_type": e.entity_type,
                "entity_id": e.entity_id,
                "user_id": str(e.user_id) if e.user_id else None,
                "source": e.source,
                "ip_address": e.ip_address,
                "detail": e.detail,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in entries
        ],
        "total": total,
    }


# --- Usage Stats ---


@router.get("/stats")
async def get_usage_stats(
    _user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get usage statistics for the dashboard."""
    # Counts by status
    print_counts = {}
    for status in ["held", "completed", "failed", "cancelled", "printing"]:
        q = select(func.count()).where(PrintJob.status == status)
        print_counts[status] = (await db.execute(q)).scalar() or 0

    scan_counts = {}
    for status in ["completed", "failed", "scanning"]:
        q = select(func.count()).where(ScanJob.status == status)
        scan_counts[status] = (await db.execute(q)).scalar() or 0

    # Daily counts for last 30 days
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    daily_prints = await db.execute(
        select(
            cast(PrintJob.created_at, Date).label("day"),
            func.count().label("count"),
        )
        .where(PrintJob.created_at >= cutoff)
        .group_by("day")
        .order_by("day")
    )

    daily_scans = await db.execute(
        select(
            cast(ScanJob.created_at, Date).label("day"),
            func.count().label("count"),
        )
        .where(ScanJob.created_at >= cutoff)
        .group_by("day")
        .order_by("day")
    )

    return {
        "print_counts": print_counts,
        "scan_counts": scan_counts,
        "daily_prints": [
            {"day": str(row.day), "count": row.count} for row in daily_prints
        ],
        "daily_scans": [
            {"day": str(row.day), "count": row.count} for row in daily_scans
        ],
    }


# --- Backup / Restore ---


@router.get("/backup")
async def export_settings(
    _user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Export all app settings as JSON for backup."""
    result = await db.execute(select(AppConfig))
    configs = result.scalars().all()

    data = {}
    for c in configs:
        data[c.key] = c.value

    return {
        "settings": data,
        "exported_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/restore")
async def restore_settings(
    body: dict,
    _user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Restore app settings from a backup JSON.

    Body: {"settings": {"key": "value", ...}}
    """
    incoming = body.get("settings")
    if not isinstance(incoming, dict):
        raise HTTPException(status_code=400, detail="Body must contain a 'settings' dict")

    restored = 0
    for key, value in incoming.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        result = await db.execute(select(AppConfig).where(AppConfig.key == key))
        existing = result.scalar_one_or_none()
        if existing:
            existing.value = value
        else:
            db.add(AppConfig(key=key, value=value))
        restored += 1

    await db.commit()
    return {"restored": restored}


# --- Retention ---


@router.post("/retention")
async def trigger_retention(
    _user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Manually trigger retention cleanup."""
    from app.routers.settings import get_setting, safe_int_setting
    from app.services.retention_service import run_retention

    scan_days = safe_int_setting(await get_setting(db, "scan_retention_days"), 7)
    print_days = safe_int_setting(await get_setting(db, "print_retention_days"), 30)

    result = await run_retention(db, scan_days=scan_days, print_days=print_days)
    return result


# --- User Management ---


class UserRoleUpdate(BaseModel):
    role: str = Field(pattern=r"^(user|admin)$")


@router.get("/users")
async def list_users(
    _user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """List all users."""
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    return [
        {
            "id": str(u.id),
            "email": u.email,
            "display_name": u.display_name,
            "role": u.role,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "last_login": u.last_login.isoformat() if u.last_login else None,
        }
        for u in result.scalars()
    ]


@router.patch("/users/{user_id}")
async def update_user_role(
    user_id: uuid_mod.UUID,
    body: UserRoleUpdate,
    _user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Update a user's role."""
    target = await db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.id == _user.id:
        raise HTTPException(status_code=400, detail="Cannot change your own role")
    target.role = body.role
    await db.commit()
    return {"id": str(target.id), "role": target.role}


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(
    user_id: uuid_mod.UUID,
    _user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a user."""
    target = await db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.id == _user.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    await db.delete(target)
    await db.commit()
