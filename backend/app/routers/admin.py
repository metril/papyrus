"""Admin endpoints: audit log, usage stats, backup/restore, retention, user management."""

import uuid as uuid_mod
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_admin
from app.database import get_db
from app.models import AppConfig, AuditEntry, PrintJob, ScanJob, User
from app.services import settings_cache

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

PRINT_JOB_STATUSES = ["held", "completed", "failed", "cancelled", "printing"]
SCAN_JOB_STATUSES = ["completed", "failed", "scanning"]

TREND_DAYS = 30
PER_USER_TOP_N = 10


def _zero_filled_status_counts(
    rows: Any, statuses: list[str]
) -> dict[str, int]:
    """Map `(status, count)` GROUP BY rows onto a fixed set of statuses.

    Statuses not present in `rows` are zero-filled; statuses in `rows` but not
    in `statuses` are dropped — matching the legacy per-status COUNT queries,
    which only ever asked about the fixed list below.
    """
    counts = dict.fromkeys(statuses, 0)
    for status, count in rows:
        if status in counts:
            counts[status] = count
    return counts


def _zero_filled_trend(
    print_rows: Any, scan_rows: Any, days: list[str]
) -> list[dict[str, Any]]:
    """Merge `(day, count)` GROUP BY rows from PrintJob/ScanJob onto a fixed
    list of UTC calendar day strings (`days`, oldest first).

    `day` is a `datetime.date` (already converted to a UTC calendar day by
    the caller's query). Any day in `days` with no rows in either query is
    zero-filled, so a trend chart never has to skip a day.
    """
    print_counts = {day.isoformat(): count for day, count in print_rows}
    scan_counts = {day.isoformat(): count for day, count in scan_rows}
    return [
        {"date": day, "prints": print_counts.get(day, 0), "scans": scan_counts.get(day, 0)}
        for day in days
    ]


def _user_label(user_id: Any, username: str | None, email: str | None) -> str:
    """Display label for a per-user stats bucket.

    A NULL `user_id` (network print, or eSCL network scan — neither has an
    authenticated user) buckets as `"Network"`. Otherwise prefer `username`,
    falling back to `email` for accounts that never set one (e.g. OIDC-only
    logins never populate the local-auth `username` column).
    """
    if user_id is None:
        return "Network"
    return username or email or str(user_id)


def _ranked_per_user(
    print_rows: Any, scan_rows: Any, *, top_n: int
) -> list[dict[str, Any]]:
    """Merge per-user `(user_id, username, email, count)` GROUP BY rows from
    both tables, sort by total (prints + scans) descending, and roll every
    row past `top_n` into a trailing `"Other"` row.

    Ties break on username so the ordering is deterministic.
    """
    by_user: dict[Any, dict[str, Any]] = {}

    def _bucket(user_id: Any, username: str | None, email: str | None) -> dict[str, Any]:
        return by_user.setdefault(
            user_id,
            {"username": _user_label(user_id, username, email), "prints": 0, "scans": 0},
        )

    for user_id, username, email, count in print_rows:
        _bucket(user_id, username, email)["prints"] += count
    for user_id, username, email, count in scan_rows:
        _bucket(user_id, username, email)["scans"] += count

    ranked = sorted(
        by_user.values(),
        key=lambda row: (-(row["prints"] + row["scans"]), row["username"]),
    )

    if len(ranked) <= top_n:
        return ranked

    head, tail = ranked[:top_n], ranked[top_n:]
    head.append(
        {
            "username": "Other",
            "prints": sum(row["prints"] for row in tail),
            "scans": sum(row["scans"] for row in tail),
        }
    )
    return head


@router.get("/stats")
async def get_usage_stats(
    _user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get usage statistics for the dashboard."""
    # Counts by status — one GROUP BY query per table instead of one COUNT
    # query per status.
    print_status_rows = await db.execute(
        select(PrintJob.status, func.count()).group_by(PrintJob.status)
    )
    print_counts = _zero_filled_status_counts(print_status_rows.all(), PRINT_JOB_STATUSES)

    scan_status_rows = await db.execute(
        select(ScanJob.status, func.count()).group_by(ScanJob.status)
    )
    scan_counts = _zero_filled_status_counts(scan_status_rows.all(), SCAN_JOB_STATUSES)

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

    # 30-day trend: one row per UTC calendar day, zero-filled so a trend
    # chart never has to skip a day. `created_at` is stored as an instant
    # (timestamptz); `timezone("UTC", ...)` reinterprets it as a naive UTC
    # wall-clock timestamp before casting to Date, so day boundaries are UTC
    # midnight regardless of the DB session's TimeZone setting.
    today_utc = datetime.now(timezone.utc).date()
    oldest_trend_day = today_utc - timedelta(days=TREND_DAYS - 1)
    trend_cutoff = datetime(
        oldest_trend_day.year, oldest_trend_day.month, oldest_trend_day.day, tzinfo=timezone.utc
    )
    trend_days = [
        (oldest_trend_day + timedelta(days=offset)).isoformat() for offset in range(TREND_DAYS)
    ]

    print_trend_rows = await db.execute(
        select(
            cast(func.timezone("UTC", PrintJob.created_at), Date).label("day"),
            func.count().label("count"),
        )
        .where(PrintJob.created_at >= trend_cutoff)
        .group_by("day")
    )
    scan_trend_rows = await db.execute(
        select(
            cast(func.timezone("UTC", ScanJob.created_at), Date).label("day"),
            func.count().label("count"),
        )
        .where(ScanJob.created_at >= trend_cutoff)
        .group_by("day")
    )
    trend_30d = _zero_filled_trend(print_trend_rows.all(), scan_trend_rows.all(), trend_days)

    # Per-user totals: outer join to User for the username label so jobs with
    # no matching/authenticated user (NULL user_id) still group into a single
    # "Network" bucket instead of being dropped.
    print_user_rows = await db.execute(
        select(PrintJob.user_id, User.username, User.email, func.count().label("count"))
        .select_from(PrintJob)
        .outerjoin(User, PrintJob.user_id == User.id)
        .group_by(PrintJob.user_id, User.username, User.email)
    )
    scan_user_rows = await db.execute(
        select(ScanJob.user_id, User.username, User.email, func.count().label("count"))
        .select_from(ScanJob)
        .outerjoin(User, ScanJob.user_id == User.id)
        .group_by(ScanJob.user_id, User.username, User.email)
    )
    per_user = _ranked_per_user(print_user_rows.all(), scan_user_rows.all(), top_n=PER_USER_TOP_N)

    return {
        "print_counts": print_counts,
        "scan_counts": scan_counts,
        "daily_prints": [
            {"day": str(row.day), "count": row.count} for row in daily_prints
        ],
        "daily_scans": [
            {"day": str(row.day), "count": row.count} for row in daily_scans
        ],
        "trend_30d": trend_30d,
        "per_user": per_user,
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
    # Restore can touch an arbitrary/unknown set of keys (any AppConfig row from the
    # backup, including encrypted ones) — invalidate everything rather than try to
    # map row keys back to logical setting names.
    settings_cache.invalidate_all()
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
