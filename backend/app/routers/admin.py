"""Admin endpoints: audit log, usage stats."""

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, cast, Date
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_admin
from app.database import get_db
from app.models import AuditEntry, PrintJob, ScanJob, User

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
