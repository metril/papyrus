"""Printer supply/error alert service.

Polls every configured physical printer (network hold queues have no real
device to report on) for three conditions and fires a webhook (+ optional
email) on each *onset*:

- ``supply_low``  → ``printer.supply_low`` — a toner/ink marker strictly below the
  configured threshold (unknown levels, reported as ``-1``/absent, never alert).
- ``error``       → ``printer.error``      — a ``printer-state-reasons`` entry
  matching a jam / open-door / empty-supply marker (matched tolerantly, since
  reasons carry ``-warning``/``-error`` suffixes).
- ``offline``     → ``printer.error``      — CUPS reports the queue stopped /
  the device unreachable (``printer-state`` == 5).

Hysteresis lives in the ``alert_state`` AppConfig row as JSON
``{printer_id: {condition: bool}}``. A webhook+email fires only on a
``False`` → ``True`` transition; while a condition stays ``True`` nothing
repeats. On recovery (``True`` → ``False``) the state resets and a webhook
fires with ``resolved: true`` (no email, to keep recovery quiet). The state
is rebuilt from the live printer set each cycle, so rows for deleted printers
are pruned automatically and never accumulate.

Per-printer probing failures are swallowed (one bad printer never aborts the
sweep) and CUPS/IPP lookups are already never-raise. A settings-read or
alert_state persistence failure (i.e. the DB is down) still propagates, but
the lifespan poller wraps the whole call in except-log-continue so it can
never kill the loop.
"""

import asyncio
import json
import logging
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppConfig, Printer
from app.services.cups_service import CupsService
from app.services.email_service import email_service
from app.services.ipp_client import probe_ipp
from app.services.webhook_service import dispatch_webhook

logger = logging.getLogger(__name__)

_ALERT_STATE_KEY = "alert_state"

# state_reasons prefixes that mean a hard printer error. Matched case-insensitively
# against the start of each reason so the CUPS ``-warning``/``-error`` suffixes
# (e.g. ``media-jam-warning``, ``toner-empty-error``) still match.
_ERROR_REASON_PREFIXES = (
    "media-jam",
    "door-open",
    "cover-open",
    "toner-empty",
    "marker-supply-empty",
)

# Shape used when CUPS lookup fails outright (mirrors CupsService's own
# unreachable fallback: printer-state 5 == stopped).
_OFFLINE_FALLBACK = {
    "state": 5,
    "state_message": "Unavailable",
    "accepting_jobs": False,
    "markers": [],
    "state_reasons": [],
}


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("true", "1", "yes")


async def _cups_reachable() -> bool:
    """Best-effort probe that cupsd itself is up, independent of any single
    printer (F126).

    ``CupsService.get_printer_status`` folds *any* failure (including cupsd
    being completely down, not just one unreachable printer) into the same
    printer-state-5 fallback, which ``_cups_status`` below also swallows into
    ``_OFFLINE_FALLBACK`` -- so a cupsd restart mid-sweep used to make every
    printer transition False->True on "offline" independently, firing a full
    round of false alarms from one hiccup instead of carrying prior state
    forward. Calling this once per sweep (mirrors the same
    ``cups.Connection().getPrinters()`` health-probe idiom already used by
    ``main._reconcile_on_startup`` and ``routers.system``) distinguishes
    "CUPS itself is unreachable" from "this one printer reported an IPP
    error", so ``check_alerts`` can skip the whole sweep on the former.
    """
    import cups

    def _probe() -> None:
        cups.Connection().getPrinters()

    try:
        await asyncio.to_thread(_probe)
        return True
    except Exception as exc:
        logger.warning("Alert sweep: CUPS is unreachable, skipping this cycle: %s", exc)
        return False


async def _cups_status(cups_name: str) -> dict:
    """CUPS status via the cached helper; never raises (offline fallback).

    F34: ``cups_name`` is the fake ``papyrus:/`` hold queue -- no device
    behind it, so it always reports idle/no-markers. Query the ``_release``
    queue instead, the only one actually bound to the device URI. Every
    caller here is a physical printer (``check_alerts`` filters out network
    queues before calling this), so the ``_release`` sibling always exists.
    """
    try:
        return await CupsService(printer_name=f"{cups_name}_release").get_printer_status()
    except Exception:
        return dict(_OFFLINE_FALLBACK)


async def _probe_if_ip(uri: str | None) -> dict | None:
    """Probe the host behind an ``ipp``/``ipps`` URI for enrichment, or None.

    ``probe_ipp`` never raises, but the URL parse is guarded too so a malformed
    stored URI can't abort the cycle.
    """
    try:
        parsed = urlparse(uri or "")
        if parsed.scheme not in ("ipp", "ipps") or not parsed.hostname:
            return None
        return await probe_ipp(parsed.hostname)
    except Exception:
        return None


def _normalize_marker_name(name: object) -> str | None:
    """Case/whitespace-insensitive key for deduping a marker name, or None
    if `name` isn't a usable string."""
    if not isinstance(name, str):
        return None
    normalized = name.strip().lower()
    return normalized or None


def _is_known_level(level: object) -> bool:
    """True if `level` is a usable (non-negative, non-bool) integer marker
    reading. CUPS/IPP report -1 (or omit the level entirely, normalized to
    -1 by the caller) for "unknown"."""
    return isinstance(level, int) and not isinstance(level, bool) and level >= 0


def _collect_marker_pairs(status: dict, ipp: dict | None) -> list[tuple[str | None, object]]:
    """Merge (name, level) marker pairs from the CUPS status and IPP probe.

    F127: both sources read the same underlying ``marker-names``/
    ``marker-levels`` device attributes (CUPS via the queue's cached printer
    object, IPP via a direct probe of the device), so a printer visible to
    both used to be listed twice -- one physical cartridge showing up as two
    "low supply" entries in the message and webhook payload. Pairs are
    merged on a normalized (stripped, lowercased) name: CUPS entries are
    added first, and a same-named IPP entry is only kept when either (a) the
    normalized name isn't already covered -- IPP fills a *name* gap CUPS
    didn't report -- or (b) it is covered but with an unusable level (CUPS
    reported the marker with no/-1 level) while the IPP entry has a usable
    one -- IPP fills a *level* gap too. Without (b), a printer whose CUPS
    queue reports a marker with an unknown level would keep that unusable
    entry and silently swallow a real low-supply reading the IPP probe did
    have for the same cartridge.
    """
    merged: dict[str, tuple[str | None, object]] = {}
    unnamed: list[tuple[str | None, object]] = []

    def _add(name: str | None, level: object) -> None:
        key = _normalize_marker_name(name)
        if key is None:
            unnamed.append((name, level))
            return
        if key not in merged or (_is_known_level(level) and not _is_known_level(merged[key][1])):
            merged[key] = (name, level)

    for m in status.get("markers") or []:
        _add(m.get("name"), m.get("level"))

    if ipp:
        markers = ipp.get("markers") or {}
        names = markers.get("names") or []
        levels = markers.get("levels") or []
        for i, name in enumerate(names):
            _add(name, levels[i] if i < len(levels) else -1)

    return list(merged.values()) + unnamed


def _low_markers(
    pairs: list[tuple[str | None, object]], threshold: int
) -> list[tuple[str | None, int]]:
    """Markers with a *known* level strictly below the threshold.

    Unknown levels (-1, negative, non-int, bool) are ignored — never an alert.
    """
    low: list[tuple[str | None, int]] = []
    for name, level in pairs:
        if not _is_known_level(level):
            continue
        if level < threshold:
            low.append((name, level))
    return low


def _error_reasons(status: dict, ipp: dict | None) -> list[str]:
    """Distinct state_reasons (from CUPS + IPP) that match an error prefix."""
    reasons: list[str] = list(status.get("state_reasons") or [])
    if ipp:
        reasons += list(ipp.get("state_reasons") or [])
    matched: list[str] = []
    for r in reasons:
        if not isinstance(r, str):
            continue
        if r.lower().startswith(_ERROR_REASON_PREFIXES) and r not in matched:
            matched.append(r)
    return matched


def _evaluate(printer: Printer, status: dict, ipp: dict | None, threshold: int) -> dict[str, dict]:
    """Evaluate all three conditions for one printer.

    Returns ``{condition: {"active": bool, "event": str, "message": str,
    "data": {...}}}`` with every condition key always present (a definite
    bool), so recovery is detected even when the underlying signal disappears.
    """
    low = _low_markers(_collect_marker_pairs(status, ipp), threshold)
    errors = _error_reasons(status, ipp)
    offline = status.get("state") == 5

    if low:
        supply_msg = ", ".join(f"{name or 'toner'} at {level}%" for name, level in low)
    else:
        supply_msg = ""

    return {
        "supply_low": {
            "active": bool(low),
            "event": "printer.supply_low",
            "message": f"Low supply: {supply_msg}" if low else "",
            "data": {"markers": [{"name": n, "level": lv} for n, lv in low]},
        },
        "error": {
            "active": bool(errors),
            "event": "printer.error",
            "message": f"Printer error: {', '.join(errors)}" if errors else "",
            "data": {"reason": "state_reasons", "state_reasons": errors},
        },
        "offline": {
            "active": offline,
            "event": "printer.error",
            "message": "Printer is offline or stopped",
            "data": {"reason": "offline"},
        },
    }


async def _load_alert_state(db: AsyncSession) -> dict:
    """Read the persisted hysteresis map; tolerant of a missing/corrupt row."""
    row = await db.get(AppConfig, _ALERT_STATE_KEY)
    if not row or not row.value:
        return {}
    try:
        data = json.loads(row.value)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


async def _save_alert_state(db: AsyncSession, state: dict) -> None:
    payload = json.dumps(state)
    row = await db.get(AppConfig, _ALERT_STATE_KEY)
    if row:
        row.value = payload
    else:
        db.add(AppConfig(key=_ALERT_STATE_KEY, value=payload))
    await db.commit()


async def _dispatch(
    db: AsyncSession,
    printer: Printer,
    cond: dict,
    *,
    resolved: bool,
    alert_email: str,
) -> None:
    """Fire the webhook (always) and, on onset only, the email (best-effort)."""
    data = {
        "printer_id": printer.id,
        "display_name": printer.display_name,
        "cups_name": printer.cups_name,
        "resolved": resolved,
        "message": cond["message"],
        **cond["data"],
    }
    await dispatch_webhook(db, cond["event"], data)

    # Recovery notifications are webhook-only to avoid a second round of mail.
    if resolved or not alert_email:
        return
    subject = f"Papyrus alert: {printer.display_name} — {cond['message']}"
    body = (
        f"Printer: {printer.display_name} ({printer.cups_name})\n"
        f"{cond['message']}\n"
    )
    try:
        await email_service.send_alert(db, alert_email, subject, body)
    except Exception as exc:
        # Mail must never suppress the (already-sent) webhook or break the poll.
        logger.warning("Alert email to %s failed: %s", alert_email, exc)


async def check_alerts(db: AsyncSession) -> None:
    """Poll every physical printer and fire alerts on condition onsets.

    A no-op when ``alerts_enabled`` is falsy. Per-printer evaluation errors are
    logged and skipped (prior state carried forward); a DB failure reading
    settings or persisting alert_state propagates to the poller's guard.
    """
    # Imported here (not at module top) to avoid a circular import: the
    # settings router imports audit/crypto which pull services back in.
    from app.routers.settings import get_setting, safe_int_setting

    if not _truthy(await get_setting(db, "alerts_enabled")):
        return

    threshold = safe_int_setting(await get_setting(db, "alert_toner_threshold"), 20)
    alert_email = (await get_setting(db, "alert_email") or "").strip()

    result = await db.execute(select(Printer).where(Printer.is_network_queue.is_(False)))
    printers = list(result.scalars())

    # F126: a cupsd hiccup makes CupsService.get_printer_status fail
    # identically for every printer, each independently folded into the same
    # offline fallback -- without this check that fires a false "offline"
    # onset per printer instead of carrying prior state forward untouched.
    if printers and not await _cups_reachable():
        return

    prev_state = await _load_alert_state(db)
    # Seeded with every live printer's *persisted* state up front -- not
    # built up empty as the loop goes. F128's per-printer save writes
    # `new_state` in full each time; if it started empty, an interrupted
    # sweep's incremental save would truncate the row to only the printers
    # visited so far, silently discarding the True flags of printers not
    # yet reached this cycle (they'd then re-fire their onsets on the very
    # next poll). Seeding from `printers` (not from `prev_state`'s keys)
    # still prunes deleted printers, since only live printers get a key here.
    new_state: dict[str, dict] = {
        str(printer.id): prev_state.get(str(printer.id), {}) for printer in printers
    }

    for printer in printers:
        pid = str(printer.id)
        try:
            status = await _cups_status(printer.cups_name)
            ipp = await _probe_if_ip(printer.uri)
            conditions = _evaluate(printer, status, ipp, threshold)
        except Exception as exc:
            # Never let one printer abort the sweep; carry its prior state
            # forward unchanged so a transient probe glitch doesn't spuriously
            # "recover" a still-active condition.
            logger.warning("Alert evaluation failed for '%s': %s", printer.cups_name, exc)
            new_state[pid] = prev_state.get(pid, {})
            await _save_alert_state(db, new_state)
            continue

        prev = prev_state.get(pid, {})
        cond_state: dict[str, bool] = {}
        for key, cond in conditions.items():
            active = bool(cond["active"])
            was_active = bool(prev.get(key, False))
            cond_state[key] = active
            if active and not was_active:
                await _dispatch(db, printer, cond, resolved=False, alert_email=alert_email)
            elif was_active and not active:
                await _dispatch(db, printer, cond, resolved=True, alert_email=alert_email)
        new_state[pid] = cond_state

        # F128: persisted after *this* printer's transitions, not only once
        # at the very end of the sweep -- a crash (or a failed write) later
        # in the loop must not lose the printers already processed and
        # already-dispatched this cycle, which would otherwise re-fire their
        # onsets on every subsequent poll/restart.
        await _save_alert_state(db, new_state)

    # Empty printer list never enters the loop above -- still persist so a
    # fleet that just lost its last printer prunes the stale state (new_state
    # only ever holds live printers, so deleted ones are pruned either way).
    if not printers:
        await _save_alert_state(db, new_state)
