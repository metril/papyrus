import asyncio
import shutil
import time

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.ws import authenticate_websocket
from app.database import get_db
from app.schemas import HealthResponse
from app.services.ws_manager import ws_manager

router = APIRouter()

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


@router.get("/health", response_model=HealthResponse)
async def health_check(db: AsyncSession = Depends(get_db)):
    """Detailed health check with subsystem status."""
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
        cups_running=cups_ok,
        scanner_available=scanner_ok,
        db_connected=db_ok,
        disk_free_mb=disk_free_mb,
        uptime_seconds=uptime,
    )


@router.websocket("/ws/jobs")
async def jobs_ws(websocket: WebSocket, db: AsyncSession = Depends(get_db)):
    """WebSocket for real-time print job status updates.

    F48: broadcasts carry full job metadata for every user, so the handshake
    must be authenticated before accept() -- an unauthenticated or
    cross-origin client is closed with 1008 and never joins the channel.
    """
    user = await authenticate_websocket(websocket, db)
    if user is None:
        await websocket.close(code=1008)
        return
    await ws_manager.connect("jobs", websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect("jobs", websocket)


@router.websocket("/ws/scans")
async def scans_ws(websocket: WebSocket, db: AsyncSession = Depends(get_db)):
    """WebSocket for real-time scan list updates. See jobs_ws's F48 note."""
    user = await authenticate_websocket(websocket, db)
    if user is None:
        await websocket.close(code=1008)
        return
    await ws_manager.connect("scans", websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect("scans", websocket)


@router.websocket("/ws/printers")
async def printers_ws(websocket: WebSocket, db: AsyncSession = Depends(get_db)):
    """WebSocket for real-time printer status updates. See jobs_ws's F48 note."""
    user = await authenticate_websocket(websocket, db)
    if user is None:
        await websocket.close(code=1008)
        return
    await ws_manager.connect("printers", websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect("printers", websocket)
