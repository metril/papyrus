import asyncio
import shutil
import time

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_docs import SERVER, std_responses
from app.auth.ws import authenticate_websocket, has_ws_permission
from app.database import async_session, get_db
from app.schemas import HealthResponse
from app.services.scan_service import get_default_scanner, is_native_device, scan_service
from app.services.ws_manager import ws_manager

router = APIRouter()
# WebSocket channels are mounted separately at /api/v1/ws.
ws_router = APIRouter()

_start_time = time.monotonic()

# /health has no auth dependency (it's the unauthenticated liveness/readiness
# probe), so the CUPS connection + `scanimage -L` fork below must not run on
# every request — a burst of anonymous hits would otherwise fork one
# scanimage process per request and saturate the shared to_thread executor,
# starving every other blocking call routed through it (CUPS status,
# release, thumbnails, ...). Cache the two probe results for
# _HEALTH_CACHE_TTL_SECONDS instead. db_ok/disk_free_mb stay uncached — they
# are cheap (one SELECT 1, one disk stat) and reflect current state.
_HEALTH_CACHE_TTL_SECONDS = 15.0
_health_cache: tuple[float, bool, bool] | None = None  # (checked_at, cups_ok, scanner_ok)

# Single-flight guard: a TTL cache alone only protects *sequential* polling —
# a burst of concurrent requests arriving before the first probe finishes and
# populates the cache would all miss it and each fork their own `scanimage`
# (the audit's literal "a few hundred concurrent anonymous GETs" scenario).
# Serializing the probe body through this lock, with the cache re-checked
# after acquiring it, makes every caller in a burst await one probe instead.
_health_probe_lock = asyncio.Lock()


def _reset_health_cache() -> None:
    """Test-only: clear the cached probe result."""
    global _health_cache
    _health_cache = None


def _cached_probe(now: float) -> tuple[bool, bool] | None:
    if _health_cache is not None and (now - _health_cache[0]) < _HEALTH_CACHE_TTL_SECONDS:
        return _health_cache[1], _health_cache[2]
    return None


async def _default_native_device() -> str | None:
    """The default scanner's device string if it is a native Brother one."""
    async def _lookup():
        async with async_session() as db:
            return await get_default_scanner(db)

    try:
        scanner = await asyncio.wait_for(_lookup(), timeout=3)
    except Exception:
        return None
    return scanner.device if scanner and is_native_device(scanner.device) else None


async def _probe_subsystems() -> tuple[bool, bool]:
    """Return `(cups_ok, scanner_ok)`, probing at most once per TTL window.

    Concurrent callers within the same window all await `_health_probe_lock`
    rather than each forking their own probe; whichever caller gets there
    first refreshes the cache, and everyone else re-checks it after
    acquiring the lock and returns that fresh result instead of probing again.
    """
    global _health_cache
    cached = _cached_probe(time.monotonic())
    if cached is not None:
        return cached

    async with _health_probe_lock:
        # Re-check: another caller may have refreshed the cache while this
        # one was waiting for the lock.
        now = time.monotonic()
        cached = _cached_probe(now)
        if cached is not None:
            return cached

        cups_ok = False
        # Check CUPS (blocking pycups call -> worker thread)
        try:
            import cups

            def _probe_cups():
                cups.Connection().getPrinters()

            await asyncio.to_thread(_probe_cups)
            cups_ok = True
        except Exception:
            pass

        scanner_ok = False
        # Check scanner
        try:
            native_device = await _default_native_device()
            if native_device:
                if scan_service.is_busy():
                    scanner_ok = True
                else:
                    result = await asyncio.wait_for(
                        scan_service.check_device(native_device), 5
                    )
                    scanner_ok = bool(result["available"])
                _health_cache = (now, cups_ok, scanner_ok)
                return cups_ok, scanner_ok
            proc = await asyncio.create_subprocess_exec(
                "scanimage", "-L",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise
            scanner_ok = b"device" in stdout.lower() if stdout else False
        except Exception:
            pass

        _health_cache = (now, cups_ok, scanner_ok)
        return cups_ok, scanner_ok


@router.get(
    "/health",
    summary="Health check",
    response_model=HealthResponse,
    responses=std_responses(SERVER),
)
async def health_check(request: Request, db: AsyncSession = Depends(get_db)):
    """Liveness/readiness probe with subsystem status.

    Public: no authentication. Reports CUPS, scanner, database and disk state
    plus uptime. `status` is `degraded` when CUPS or the database is down.
    The CUPS and scanner probes are cached for about 15 seconds.
    """
    db_ok = False
    disk_free_mb = 0

    cups_ok, scanner_ok = await _probe_subsystems()

    # Check database
    try:
        from sqlalchemy import text

        from app.database import async_session
        async with async_session() as db:
            await db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        pass

    # Disk space
    try:
        from app.routers.settings import get_setting
        scan_dir = await get_setting(db, "scan_dir") or "/app/data/scans"
        usage = shutil.disk_usage(scan_dir)
        disk_free_mb = usage.free // (1024 * 1024)
    except Exception:
        pass

    uptime = int(time.monotonic() - _start_time)
    status = "ok" if (cups_ok and db_ok) else "degraded"

    return HealthResponse(
        status=status,
        version=request.app.version,
        cups_running=cups_ok,
        scanner_available=scanner_ok,
        db_connected=db_ok,
        disk_free_mb=disk_free_mb,
        uptime_seconds=uptime,
    )


@ws_router.websocket("/jobs")
async def jobs_ws(websocket: WebSocket):
    """WebSocket for real-time print job status updates.

    F48: broadcasts carry full job metadata for every user, so the handshake
    must be authenticated -- and, like the HTTP routes serving the same
    data, permission-checked ("print") -- before accept(); an
    unauthenticated, wrong-permission, or cross-origin client is closed
    with 1008 and never joins the channel.

    The auth lookup runs in its own short-lived session
    (`async with async_session()`, not `Depends(get_db)`) so it closes
    before accept() -- a request-scoped session would otherwise stay
    checked out of the pool, idle-in-transaction, for the socket's entire
    (potentially hours-long) lifetime, and a handful of open tabs would
    exhaust the pool and block every other request.
    """
    async with async_session() as db:
        identity = await authenticate_websocket(websocket, db)
    if identity is None or not has_ws_permission(identity, "print"):
        await websocket.close(code=1008)
        return
    await ws_manager.connect("jobs", websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect("jobs", websocket)


@ws_router.websocket("/scans")
async def scans_ws(websocket: WebSocket):
    """WebSocket for real-time scan list updates. See jobs_ws's F48 note
    (permission "scan" here, and the same short-lived auth session)."""
    async with async_session() as db:
        identity = await authenticate_websocket(websocket, db)
    if identity is None or not has_ws_permission(identity, "scan"):
        await websocket.close(code=1008)
        return
    await ws_manager.connect("scans", websocket, identity)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect("scans", websocket)


@ws_router.websocket("/printers")
async def printers_ws(websocket: WebSocket):
    """WebSocket for real-time printer status updates. See jobs_ws's F48
    note (permission "print" here, and the same short-lived auth session)."""
    async with async_session() as db:
        identity = await authenticate_websocket(websocket, db)
    if identity is None or not has_ws_permission(identity, "print"):
        await websocket.close(code=1008)
        return
    await ws_manager.connect("printers", websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect("printers", websocket)


@ws_router.websocket("/notifications")
async def notifications_ws(websocket: WebSocket):
    """Per-user WebSocket: kiosk sign-in requests (`kiosk_login_request`/`kiosk_login_resolved`)
    and `kiosk_pin_cleared`. Sessions only (API tokens are closed with 1008); receives only
    messages addressed to its own user. Same short-lived auth session as jobs_ws."""
    async with async_session() as db:
        identity = await authenticate_websocket(websocket, db)
    if identity is None or identity.permissions is not None:
        await websocket.close(code=1008)
        return
    await ws_manager.connect("notifications", websocket, identity)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect("notifications", websocket)
