import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import FileResponse

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
    """Seed missing default settings into AppConfig (fills gaps, not all-or-nothing)."""
    from sqlalchemy import select
    from app.database import async_session
    from app.models import AppConfig
    from app.routers.settings import DEFAULTS

    async with async_session() as db:
        result = await db.execute(select(AppConfig.key))
        existing_keys = {row[0] for row in result.all()}

        added = 0
        for key, value in DEFAULTS.items():
            if key not in existing_keys:
                db.add(AppConfig(key=key, value=value))
                added += 1

        if added:
            await db.commit()
            logger.info("Seeded %d missing default settings", added)
        else:
            logger.info("AppConfig has all %d defaults — nothing to seed", len(DEFAULTS))


async def _ensure_local_admin() -> None:
    """Create local admin account if none exists and env vars are set."""
    from sqlalchemy import select
    from app.database import async_session
    from app.models import User

    async with async_session() as db:
        if not settings.admin_username or not settings.admin_password:
            return

        # Check if this local admin already exists
        result = await db.execute(
            select(User).where(User.username == settings.admin_username, User.is_local == True)
        )
        existing = result.scalar_one_or_none()
        if existing:
            # Update password if it changed
            from argon2 import PasswordHasher
            ph = PasswordHasher()
            try:
                ph.verify(existing.password_hash, settings.admin_password)
            except Exception:
                existing.password_hash = ph.hash(settings.admin_password)
                await db.commit()
                logger.info("Updated local admin password: %s", settings.admin_username)
            return

        from argon2 import PasswordHasher
        ph = PasswordHasher()
        admin = User(
            username=settings.admin_username,
            email=f"{settings.admin_username}@local",
            display_name=settings.admin_username,
            role="admin",
            is_local=True,
            password_hash=ph.hash(settings.admin_password),
            oidc_sub=None,
        )
        db.add(admin)
        await db.commit()
        logger.info("Created local admin account: %s", settings.admin_username)


async def _retention_loop() -> None:
    """Background task that runs retention cleanup once per hour."""
    from app.database import async_session
    from app.routers.settings import get_setting, safe_int_setting
    from app.services.retention_service import run_retention

    while True:
        await asyncio.sleep(3600)  # every hour
        try:
            async with async_session() as db:
                scan_days = safe_int_setting(await get_setting(db, "scan_retention_days"), 7)
                print_days = safe_int_setting(await get_setting(db, "print_retention_days"), 30)
                await run_retention(db, scan_days=scan_days, print_days=print_days)
        except Exception as exc:
            logger.warning("Retention cleanup failed: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: seed defaults, create dirs, reconcile hardware
    if not settings.encryption_key:
        logger.warning(
            "PAPYRUS_ENCRYPTION_KEY is not set — encrypted settings "
            "(SMTP password, OAuth secrets, etc.) cannot be stored"
        )
    await _seed_defaults()

    from app.database import async_session
    from app.routers.settings import get_setting
    async with async_session() as db:
        scan_dir = await get_setting(db, "scan_dir") or "/app/data/scans"
        upload_dir = await get_setting(db, "upload_dir") or "/app/data/uploads"
    os.makedirs(scan_dir, exist_ok=True)
    os.makedirs(upload_dir, exist_ok=True)

    await _ensure_local_admin()
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
# All static files (including /assets/*) are served through spa_fallback.
# This avoids StaticFiles returning JSON 404s for stale hashed asset requests.
static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
if os.path.isdir(static_dir):
    @app.get("/{path:path}")
    async def spa_fallback(path: str):
        file_path = os.path.join(static_dir, path)
        if path and os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(static_dir, "index.html"))
