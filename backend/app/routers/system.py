import asyncio
import os
import shutil
import time

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas import HealthResponse
from app.services.ws_manager import ws_manager

router = APIRouter()

_start_time = time.monotonic()


@router.get("/health", response_model=HealthResponse)
async def health_check(db: AsyncSession = Depends(get_db)):
    """Detailed health check with subsystem status."""
    cups_ok = False
    scanner_ok = False
    db_ok = False
    disk_free_mb = 0

    # Check CUPS
    try:
        import cups
        conn = cups.Connection()
        conn.getPrinters()
        cups_ok = True
    except Exception:
        pass

    # Check scanner
    try:
        proc = await asyncio.create_subprocess_exec(
            "scanimage", "-L",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        scanner_ok = b"device" in stdout.lower() if stdout else False
    except Exception:
        pass

    # Check database
    try:
        from app.database import async_session
        from sqlalchemy import text
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
async def jobs_ws(websocket: WebSocket):
    """WebSocket for real-time print job status updates."""
    await ws_manager.connect("jobs", websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect("jobs", websocket)


@router.websocket("/ws/scans")
async def scans_ws(websocket: WebSocket):
    """WebSocket for real-time scan list updates."""
    await ws_manager.connect("scans", websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect("scans", websocket)
