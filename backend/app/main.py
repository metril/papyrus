import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import FileResponse

from app.config import settings
from app.logging_config import setup_logging
from app.middleware import RequestIDMiddleware
from app.routers import (
    admin,
    auth,
    cloud,
    copy,
    email,
    escl,
    jobs,
    printer,
    printers,
    scanner,
    scanners,
    smb,
    system,
    webdav,
    webhooks,
)
from app.routers import settings as settings_router

setup_logging(json_logs=not settings.dev_mode)

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
        def _list_existing_cups() -> set[str]:
            return set(cups.Connection().getPrinters().keys())

        try:
            existing_cups = await asyncio.to_thread(_list_existing_cups)
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
            from app.services import settings_cache

            settings_cache.invalidate_all()
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
            select(User).where(User.username == settings.admin_username, User.is_local.is_(True))
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


# In-memory snapshot of the last-broadcast status per CUPS queue name. Lives
# at module scope (not inside the loop) so `_broadcast_changed_printer_statuses`
# is unit-testable in isolation, and because a single-worker deployment makes
# an in-process cache safe.
_printer_status_previous: dict[str, dict] = {}


async def _broadcast_changed_printer_statuses(
    current: dict[str, dict], previous: dict[str, dict]
) -> None:
    """Compare each printer's current status snapshot to the previous one and
    broadcast a ``printer_status`` WS event only for printers whose status
    changed. Mutates ``previous`` in place with this cycle's snapshot.

    Extracted from ``_poll_printer_statuses`` so the change-detection logic is
    unit-testable without a DB session or a real CUPS connection.
    """
    from app.services.ws_manager import ws_manager

    for cups_name, status in current.items():
        if previous.get(cups_name) != status:
            await ws_manager.broadcast("printers", {
                "type": "printer_status",
                "data": status,
            })
    previous.clear()
    previous.update(current)


async def _poll_printer_statuses(printers: list) -> None:
    """Fetch current CUPS status for each given physical printer and
    broadcast any changes since the last poll.

    Takes an explicit ``printers`` list (objects with ``id``/``cups_name``)
    rather than querying the DB itself, so tests can exercise this with fake
    printer objects and a fake CupsService without a database.

    A failure fetching one printer's status (anything beyond the IPPError
    that CupsService already maps to its fallback shape — e.g. a RuntimeError
    from ``cups.Connection()`` during a cupsd hiccup) must not abort the
    cycle: the erroring printer is skipped this cycle (no snapshot entry, so
    its recovery is detected as a change and broadcast once it responds
    again) while the remaining printers are compared/broadcast normally.
    """
    from app.services.cups_service import CupsService

    current: dict[str, dict] = {}
    for p in printers:
        try:
            status = await CupsService(printer_name=p.cups_name).get_printer_status()
        except Exception as exc:
            logger.warning("Status poll failed for printer '%s': %s", p.cups_name, exc)
            continue
        current[p.cups_name] = {"id": p.id, "cups_name": p.cups_name, **status}

    await _broadcast_changed_printer_statuses(current, _printer_status_previous)


async def _printer_status_loop() -> None:
    """Background task that pushes printer status changes over WS every 15s.

    Only physical printers are polled (network queues aren't real devices
    with toner/state to report). ``get_printer_status`` is served from
    CupsService's 12s cache, but since 15s > 12s the cache has always expired
    by the next poll, so each cycle performs exactly one real CUPS round-trip
    per printer — the cache instead absorbs concurrent GET /printer/status
    requests from clients between polls.
    """
    from sqlalchemy import select

    from app.database import async_session
    from app.models import Printer

    while True:
        await asyncio.sleep(15)
        try:
            async with async_session() as db:
                result = await db.execute(
                    select(Printer).where(Printer.is_network_queue.is_(False))
                )
                printers = list(result.scalars())
            await _poll_printer_statuses(printers)
        except Exception as exc:
            logger.warning("Printer status poll failed: %s", exc)


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
    printer_status_task = asyncio.create_task(_printer_status_loop())
    yield
    # Shutdown
    retention_task.cancel()
    printer_status_task.cancel()
    from app.services.http_client import close_http_client

    await close_http_client()


app = FastAPI(
    title="Papyrus",
    description="Web-based print and scan server",
    version="0.1.0",
    lifespan=lifespan,
)

# Session middleware for OIDC (must be added before CORS)
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)

# Only add CORS if explicit origins are configured — the app is same-origin
# behind Traefik by default, and allow_origins=["*"] is spec-invalid together
# with allow_credentials=True.
if settings.cors_origins_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Request-ID middleware, added last so it runs outermost (Starlette runs
# middleware in reverse-add order) — every request/response, including
# session/CORS handling, gets a request ID.
app.add_middleware(RequestIDMiddleware)

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
