import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import FileResponse

from app.auth.oidc import setup_oauth
from app.config import settings
from app.routers import admin, auth, cloud, copy, email, escl, jobs, printer, printers, scanner, scanners, settings as settings_router, smb, system, webdav, webhooks

logger = logging.getLogger(__name__)


async def _reconcile_on_startup() -> None:
    """Restore CUPS queues and sane-airscan device configs from the DB on startup."""
    import cups
    from sqlalchemy import select
    from app.database import async_session
    from app.models import Printer, Scanner
    from app.services import cups_admin

    async with async_session() as db:
        # --- CUPS printer queues ---
        try:
            existing_cups = set(cups.Connection().getPrinters().keys())
        except Exception:
            existing_cups = set()

        result = await db.execute(select(Printer))
        for printer_obj in result.scalars():
            if printer_obj.cups_name in existing_cups:
                continue
            try:
                if printer_obj.is_network_queue:
                    await cups_admin.add_network_queue(
                        printer_obj.cups_name, printer_obj.display_name
                    )
                else:
                    await cups_admin.add_physical_printer(
                        printer_obj.cups_name, printer_obj.display_name, printer_obj.uri
                    )
                logger.info("Restored CUPS queue: %s", printer_obj.cups_name)
            except Exception as exc:
                logger.warning(
                    "Failed to restore CUPS queue '%s': %s", printer_obj.cups_name, exc
                )

        # --- brscan4 registrations ---
        result = await db.execute(select(Scanner))
        for scanner_obj in result.scalars():
            cfg = scanner_obj.post_scan_config or {}
            model = cfg.get("brother_model")
            ip = cfg.get("brother_ip")
            if not model or not ip:
                continue
            try:
                proc = await asyncio.create_subprocess_exec(
                    "brsaneconfig4", "-a",
                    f"name={scanner_obj.name}", f"model={model}", f"ip={ip}",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(proc.communicate(), timeout=10)
                logger.info("Restored brscan4 registration: %s", scanner_obj.name)
            except Exception as exc:
                logger.warning(
                    "Failed to restore brscan4 '%s': %s", scanner_obj.name, exc
                )



async def _seed_defaults() -> None:
    """Seed default settings into AppConfig on first run (empty table)."""
    from sqlalchemy import select, func
    from app.database import async_session
    from app.models import AppConfig

    defaults = {
        "scan_dir": "/app/data/scans",
        "upload_dir": "/app/data/uploads",
        "max_upload_size_mb": "50",
        "scan_retention_days": "7",
        "print_retention_days": "30",
        "scan_filename_template": "scan_{date}_{time}_{id}",
        "dev_mode": "false",
        "require_release_pin": "false",
        "smtp_port": "587",
        "ocr_enabled": "false",
        "ocr_language": "eng",
        "ftp_port": "21",
        "ftp_remote_dir": "/",
        "ftp_protocol": "ftp",
        "email_webhook_rate_limit": "10",
        "escl_enabled": "true",
        "oidc_scopes": "openid email profile",
        "oidc_groups_claim": "groups",
    }

    async with async_session() as db:
        count = (await db.execute(select(func.count()).select_from(AppConfig))).scalar() or 0
        if count > 0:
            return  # Already initialized

        for key, value in defaults.items():
            db.add(AppConfig(key=key, value=value))
        await db.commit()
        logger.info("Seeded %d default settings", len(defaults))


async def _retention_loop() -> None:
    """Background task that runs retention cleanup once per hour."""
    from app.database import async_session
    from app.routers.settings import get_setting
    from app.services.retention_service import run_retention

    while True:
        await asyncio.sleep(3600)  # every hour
        try:
            async with async_session() as db:
                scan_days = int(await get_setting(db, "scan_retention_days") or 7)
                print_days = int(await get_setting(db, "print_retention_days") or 30)
                await run_retention(db, scan_days=scan_days, print_days=print_days)
        except Exception as exc:
            logger.warning("Retention cleanup failed: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: seed defaults, create dirs, reconcile hardware
    await _seed_defaults()

    from app.database import async_session
    from app.routers.settings import get_setting
    async with async_session() as db:
        scan_dir = await get_setting(db, "scan_dir") or "/app/data/scans"
        upload_dir = await get_setting(db, "upload_dir") or "/app/data/uploads"
    os.makedirs(scan_dir, exist_ok=True)
    os.makedirs(upload_dir, exist_ok=True)

    setup_oauth()
    await _reconcile_on_startup()
    retention_task = asyncio.create_task(_retention_loop())
    yield
    # Shutdown
    retention_task.cancel()


app = FastAPI(
    title="Papyrus",
    description="Web-based print and scan server",
    version="0.1.0",
    lifespan=lifespan,
)

# Session middleware for OIDC (must be added before CORS)
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tightened in production via reverse proxy
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes
app.include_router(system.router, prefix="/api/system", tags=["system"])
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(printer.router, prefix="/api/printer", tags=["printer"])
app.include_router(jobs.router, prefix="/api/jobs", tags=["jobs"])
app.include_router(scanner.router, prefix="/api/scanner", tags=["scanner"])
app.include_router(copy.router, prefix="/api/copy", tags=["copy"])
app.include_router(smb.router, prefix="/api/smb", tags=["smb"])
app.include_router(email.router, prefix="/api/email", tags=["email"])
app.include_router(cloud.router, prefix="/api/cloud", tags=["cloud"])
app.include_router(settings_router.router, prefix="/api/settings", tags=["settings"])
app.include_router(printers.router, prefix="/api/printers", tags=["printers"])
app.include_router(scanners.router, prefix="/api/scanners", tags=["scanners"])
app.include_router(admin.router, prefix="/api/admin", tags=["admin"])
app.include_router(webdav.router, prefix="/api/webdav", tags=["webdav"])
app.include_router(webhooks.router, prefix="/api/webhooks", tags=["webhooks"])

# eSCL scanner protocol (no /api prefix — clients expect /eSCL/ at root)
app.include_router(escl.router, tags=["escl"])

# Serve frontend static files (built React app) with SPA fallback
static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
if os.path.isdir(static_dir):
    assets_dir = os.path.join(static_dir, "assets")
    if os.path.isdir(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="static-assets")

    @app.get("/{path:path}")
    async def spa_fallback(path: str):
        file_path = os.path.join(static_dir, path)
        if path and os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(static_dir, "index.html"))
