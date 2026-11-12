"""Admin endpoints: audit log, usage stats, backup/restore, retention, user management."""

import uuid as uuid_mod
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Date, cast, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_docs import (
    AUTH_ERRORS,
    CONFLICT,
    NOT_FOUND,
    error_group,
    file_response,
    std_responses,
)
from app.auth.dependencies import require_admin
from app.database import get_db
from app.exceptions import ConflictError
from app.models import APIToken, AppConfig, AuditEntry, DeviceAuthorization, PrintJob, ScanJob, User
from app.schemas import (
    AdminUserResponse,
    AuditEntryResponse,
    BackupResponse,
    Page,
    RestoreResult,
    RetentionResult,
    UsageStats,
    UserRoleResult,
    UserRoleUpdate,
)
from app.services import settings_cache
from app.services.audit_service import log_event
from app.services.kiosk_login import notify as kiosk_notify
from app.services.user_deletion import reassign_jobs
from app.services.ws_manager import ws_manager

router = APIRouter()

_BAD = error_group(400, "The request is not acceptable in the current state.")


# --- Audit Log ---


@router.get(
    "/audit",
    summary="List audit log entries",
    response_model=Page[AuditEntryResponse],
    responses=std_responses(AUTH_ERRORS),
)
async def get_audit_log(
    action: str | None = None,
    entity_type: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Page[AuditEntryResponse]:
    """Return the audit log, newest first (admin only).

    Filter by exact `action` and/or `entity_type`; paginate with `limit`
    (1-200) and `offset`.
    """
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

    return Page[AuditEntryResponse](
        items=[
            AuditEntryResponse(
                id=e.id,
                action=e.action,
                entity_type=e.entity_type,
                entity_id=e.entity_id,
                user_id=str(e.user_id) if e.user_id else None,
                source=e.source,
                ip_address=e.ip_address,
                detail=e.detail,
                created_at=e.created_at.isoformat() if e.created_at else None,
            )
            for e in entries
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


# --- Usage Stats ---

PRINT_JOB_STATUSES = ["held", "converting", "printing", "completed", "failed", "cancelled"]
SCAN_JOB_STATUSES = ["completed", "failed", "scanning", "cancelled"]

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


@router.get(
    "/stats",
    summary="Get usage statistics",
    response_model=UsageStats,
    responses=std_responses(AUTH_ERRORS),
)
async def get_usage_stats(
    _user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Usage statistics for the dashboard (admin only).

    Per-status job counts, a zero-filled 30-day UTC trend and per-user totals
    (top 10 plus an `Other` row; unauthenticated network jobs count as
    `Network`).
    """
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
        "trend_30d": trend_30d,
        "per_user": per_user,
    }


# --- Backup / Restore ---


@router.get(
    "/backup",
    summary="Export settings backup",
    response_model=BackupResponse,
    responses=file_response(["application/json"], "JSON backup of every stored setting.")
    | std_responses(AUTH_ERRORS),
)
async def export_settings(
    _user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Export all stored settings as a JSON backup (admin only).

    Returns every AppConfig row. Encrypted values remain encrypted, so the
    backup is only restorable on an instance with the same encryption key.
    """
    result = await db.execute(select(AppConfig))
    configs = result.scalars().all()

    data = {}
    for c in configs:
        data[c.key] = c.value

    return {
        "settings": data,
        "exported_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post(
    "/restore",
    summary="Restore settings from backup",
    response_model=RestoreResult,
    responses=std_responses(AUTH_ERRORS, _BAD),
)
async def restore_settings(
    body: dict,
    _user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Restore settings from a backup produced by the backup endpoint (admin only).

    Body: `{"settings": {"key": "value", ...}}`. Existing keys are overwritten,
    unknown keys are created and non-string entries are skipped. Invalidates
    the whole settings cache. 400 when `settings` is missing or not an object.
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


@router.post(
    "/retention",
    summary="Run retention cleanup now",
    response_model=RetentionResult,
    responses=std_responses(AUTH_ERRORS),
)
async def trigger_retention(
    _user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Run the scan, print and audit-log retention policies immediately (admin only).

    Uses the configured retention days and permanently deletes expired rows
    and their files.
    """
    from app.routers.settings import get_setting, safe_int_setting
    from app.services.retention_service import run_retention

    scan_days = safe_int_setting(await get_setting(db, "scan_retention_days"), 7)
    print_days = safe_int_setting(await get_setting(db, "print_retention_days"), 30)
    audit_days = safe_int_setting(await get_setting(db, "audit_retention_days"), 90)

    result = await run_retention(
        db, scan_days=scan_days, print_days=print_days, audit_days=audit_days
    )
    return result


# --- User Management ---


@router.get(
    "/users",
    summary="List users",
    response_model=list[AdminUserResponse],
    responses=std_responses(AUTH_ERRORS),
)
async def list_users(
    _user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """List all users, newest first (admin only)."""
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    return [
        {
            "id": str(u.id),
            "email": u.email,
            "display_name": u.display_name,
            "role": u.role,
            "is_active": u.is_active,
            "kiosk_enabled": u.kiosk_enabled,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "last_login": u.last_login.isoformat() if u.last_login else None,
        }
        for u in result.scalars()
    ]


@router.patch(
    "/users/{user_id}",
    summary="Update a user",
    response_model=UserRoleResult,
    responses=std_responses(AUTH_ERRORS, NOT_FOUND, CONFLICT, _BAD),
)
async def update_user_role(
    user_id: uuid_mod.UUID,
    body: UserRoleUpdate,
    _user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Change a user's `role`, `is_active` and/or `kiosk_enabled` (admin only; omitted fields are
    unchanged). 400 when changing your own role or deactivating yourself, 409 when it would
    deactivate the last active admin. Deactivating a user also denies their pending kiosk
    sign-in requests, closes their open WebSockets and blocks every session and token of theirs.
    Turning off `is_active` or `kiosk_enabled` also denies pending kiosk requests and revokes
    unexpired tokens minted by kiosk sign-in."""
    target = await db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if body.role is not None and target.id == _user.id and body.role != target.role:
        raise HTTPException(status_code=400, detail="Cannot change your own role")
    if body.is_active is False and target.is_active:
        if target.id == _user.id:
            raise HTTPException(status_code=400, detail="Cannot deactivate yourself")
        final_role = body.role if body.role is not None else target.role
        if target.role == "admin" or final_role == "admin":
            # Serialise concurrent deactivations: lock every active admin (id order), recount.
            admins = (await db.execute(
                select(User).where(User.role == "admin", User.is_active.is_(True))
                .order_by(User.id).with_for_update()
                .execution_options(populate_existing=True))).scalars().all()
            others = [a for a in admins if a.id != target.id]
            if not others:
                raise ConflictError("Cannot deactivate the last active admin")
            if _user.id not in {a.id for a in others}:
                raise HTTPException(status_code=403, detail="Admin access required")
    target = (await db.execute(
        select(User).where(User.id == user_id).with_for_update()
        .execution_options(populate_existing=True))).scalar_one()
    deactivating = body.is_active is False and target.is_active
    blocking = deactivating or (body.kiosk_enabled is False and target.kiosk_enabled)

    changes: dict[str, Any] = {}
    if body.role is not None and body.role != target.role:
        changes["role"] = body.role
    if body.is_active is not None and body.is_active != target.is_active:
        changes["is_active"] = body.is_active
    if body.kiosk_enabled is not None and body.kiosk_enabled != target.kiosk_enabled:
        changes["kiosk_enabled"] = body.kiosk_enabled
    denied: list[uuid_mod.UUID] = []
    for field, value in changes.items():
        setattr(target, field, value)
    if blocking:
        denied = list((await db.execute(
            update(DeviceAuthorization)
            .where(DeviceAuthorization.target_user_id == target.id,
                   DeviceAuthorization.status == "pending")
            .values(status="denied").returning(DeviceAuthorization.id))).scalars().all())
        minted = select(DeviceAuthorization.minted_token_id).where(
            DeviceAuthorization.minted_token_id.is_not(None))
        await db.execute(
            delete(APIToken).where(
                APIToken.user_id == target.id, APIToken.id.in_(minted),
                or_(APIToken.expires_at.is_(None),
                    APIToken.expires_at > datetime.now(timezone.utc)))
            .execution_options(synchronize_session=False))
    audit_actions = {
        "role": "user.role_change",
        "is_active": "user.activate" if changes.get("is_active") else "user.deactivate",
        "kiosk_enabled": "user.kiosk_enable" if changes.get("kiosk_enabled")
        else "user.kiosk_disable",
    }
    for field, value in changes.items():
        await log_event(db, audit_actions[field], "user", str(target.id), user_id=_user.id,
                        detail={field: value, "email": target.email})
    await db.commit()
    for req_id in denied:
        await kiosk_notify(target.id, "kiosk_login_resolved", {"id": str(req_id)})
    if deactivating:
        await ws_manager.close_user(target.id)
    return {"id": str(target.id), "role": target.role, "is_active": target.is_active,
            "kiosk_enabled": target.kiosk_enabled}


@router.delete(
    "/users/{user_id}",
    summary="Delete a user",
    status_code=204,
    responses=std_responses(AUTH_ERRORS, NOT_FOUND, _BAD),
)
async def delete_user(
    user_id: uuid_mod.UUID,
    _user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a user and cascade their cloud/SMB connections; their scan and print jobs are
    reassigned to the acting admin (admin only). 400 when targeting yourself."""
    target = await db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.id == _user.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    await reassign_jobs(db, target.id, _user.id)
    await db.delete(target)
    await db.commit()
    await ws_manager.close_user(user_id)
