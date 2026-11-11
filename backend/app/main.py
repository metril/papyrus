import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import FileResponse, HTMLResponse, JSONResponse

from app.api_docs import TAGS_METADATA, generate_unique_id, install_openapi
from app.auth.dependencies import get_current_user
from app.config import settings, validate_runtime_secrets
from app.database import get_db
from app.exceptions import register_exception_handlers
from app.legacy_alias import LegacyAliasMiddleware
from app.legacy_alias import route_exists as _route_exists
from app.logging_config import setup_logging
from app.middleware import RequestIDMiddleware
from app.rate_limit_middleware import RateLimitMiddleware
from app.routers import (
    admin,
    auth,
    cloud,
    contacts,
    copy,
    device,
    email,
    escl,
    jobs,
    kiosks,
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
    from app.models import Printer, ScanJob, Scanner
    from app.routers import scanners as scanners_router
    from app.services import cups_admin

    async with async_session() as db:
        # --- CUPS printer queues ---
        def _list_existing_cups() -> set[str]:
            return set(cups.Connection().getPrinters().keys())

        try:
            existing_cups = await asyncio.to_thread(_list_existing_cups)
        except Exception:
            existing_cups = set()

        # Built-in zero-config hold queue backing the static AirPrint advert.
        # printers.conf is not persisted across container *recreation*, but it
        # DOES survive a plain restart -- including a queue cupsd left stopped
        # or rejecting, which nothing else ever re-enables (the printer then
        # looks offline to AirPrint clients until a manual down/up). lpadmin
        # is create-or-modify, so run unconditionally: this creates the queue
        # when missing and re-enables + re-asserts printer-error-policy=
        # abort-job when it already exists.
        try:
            await cups_admin.ensure_default_queue()
            logger.info(
                "Ensured built-in default hold queue: %s",
                cups_admin.DEFAULT_QUEUE_NAME,
            )
        except Exception as exc:
            logger.error("Failed to ensure default hold queue: %s", exc)

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

        # --- scanner configs ---
        result = await db.execute(select(Scanner))
        for scanner_obj in result.scalars():
            # F23: /etc/sane.d/airscan.d/papyrus.conf is baked into the image
            # (not a persisted volume), so every airscan: scanner's entry is
            # lost on rebuild/recreate -- previously the only place this was
            # rewritten was on UI-triggered probe/test/add, so a scan against
            # a rebuilt container failed until an admin happened to hit the
            # test endpoint. Reuses the same self-healing helper GET
            # /scanners/{id}/test already calls.
            if scanner_obj.device.startswith("airscan:"):
                try:
                    scanners_router._ensure_airscan_config(
                        scanner_obj.name, scanner_obj.device, scanner_obj.post_scan_config
                    )
                    logger.info("Restored airscan config for scanner: %s", scanner_obj.name)
                except Exception as exc:
                    logger.warning(
                        "Failed to restore airscan config for '%s': %s", scanner_obj.name, exc
                    )

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

        # --- F45: orphaned "scanning" rows from a crash/restart ---
        # Only the in-flight request/task ever transitions a ScanJob out of
        # "scanning", so a container crash/redeploy mid-scan strands the row
        # there permanently: retention's status filter never selects it, and
        # the UI shows a scan that will never finish. Mark any leftovers
        # failed so they're visible and eligible for retention.
        result = await db.execute(select(ScanJob).where(ScanJob.status == "scanning"))
        stale_scans = list(result.scalars())
        if stale_scans:
            for scan in stale_scans:
                scan.status = "failed"
                scan.error_message = "Interrupted by server restart"
            await db.commit()
            logger.info("Marked %d stale scanning job(s) failed on startup", len(stale_scans))



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
        if not settings.admin_password:
            # No password means no local admin can be created. This is the
            # ordinary OIDC-only deployment shape -- docker/compose.yaml
            # defaults PAPYRUS_ADMIN_USERNAME to "admin", so username-set/
            # password-blank is what every OIDC-only compose install logs on
            # every boot, not a misconfiguration -- so this is informational,
            # not a warning.
            logger.info(
                "PAPYRUS_ADMIN_PASSWORD is not set — skipping local admin account "
                "creation (OIDC-only deployment). Set it (see .env.example) to "
                "create a local admin."
            )
            return
        if not settings.admin_username:
            # A password with no username is never enough to create an
            # account -- warn instead of silently skipping, since this is
            # very likely a deployment that meant to set one.
            logger.warning(
                "PAPYRUS_ADMIN_PASSWORD is set but PAPYRUS_ADMIN_USERNAME is empty — "
                "skipping local admin account creation. Set PAPYRUS_ADMIN_USERNAME "
                "(see .env.example) to create a local admin account."
            )
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
                await asyncio.to_thread(ph.verify, existing.password_hash, settings.admin_password)
            except Exception:
                existing.password_hash = await asyncio.to_thread(ph.hash, settings.admin_password)
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
            password_hash=await asyncio.to_thread(ph.hash, settings.admin_password),
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
                audit_days = safe_int_setting(await get_setting(db, "audit_retention_days"), 90)
                await run_retention(
                    db, scan_days=scan_days, print_days=print_days, audit_days=audit_days
                )
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
            # F34: p.cups_name is the fake papyrus:/ hold queue with the
            # generic PPD -- no device behind it, so it always reports
            # idle/no-markers. _release is the queue actually bound to the
            # device URI. Callers of this function only pass physical
            # printers (is_network_queue=False), which always have one.
            status = await CupsService(
                printer_name=f"{p.cups_name}_release"
            ).get_printer_status()
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


async def _alert_loop() -> None:
    """Background task that polls printers for supply/error alerts.

    The interval (``alert_poll_minutes``) is re-read every cycle so a change
    made in the Settings UI applies without a restart. ``check_alerts`` is
    itself never-raise, but the loop still guards with except-log-continue so a
    DB hiccup while reading the interval or running the sweep can never kill it.
    """
    from app.database import async_session
    from app.routers.settings import get_setting, safe_int_setting
    from app.services.alert_service import check_alerts

    while True:
        try:
            async with async_session() as db:
                minutes = safe_int_setting(await get_setting(db, "alert_poll_minutes"), 5)
        except Exception:
            minutes = 5
        await asyncio.sleep(max(minutes, 1) * 60)
        try:
            async with async_session() as db:
                await check_alerts(db)
        except Exception as exc:
            logger.warning("Alert check failed: %s", exc)


async def _escl_expiry_loop() -> None:
    """Expire abandoned eSCL jobs (and their held pages) without needing eSCL traffic."""
    from app.routers.escl import _purge_stale_jobs

    while True:
        await asyncio.sleep(60)
        try:
            _purge_stale_jobs()
        except Exception as exc:
            logger.warning("eSCL job expiry failed: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: validate secrets first — fail fast before touching the DB or
    # hardware if the session signing key is missing/placeholder.
    validate_runtime_secrets()
    # Seed defaults, create dirs, reconcile hardware
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
    from app.services.scan_service import remove_scan_leftovers

    removed = remove_scan_leftovers(scan_dir)
    if removed:
        logger.info("Removed %d leftover scan directories", removed)

    await _ensure_local_admin()
    await _reconcile_on_startup()
    retention_task = asyncio.create_task(_retention_loop())
    printer_status_task = asyncio.create_task(_printer_status_loop())
    alert_task = asyncio.create_task(_alert_loop())
    escl_expiry_task = asyncio.create_task(_escl_expiry_loop())
    yield
    # Shutdown
    retention_task.cancel()
    printer_status_task.cancel()
    alert_task.cancel()
    escl_expiry_task.cancel()
    from app.services.http_client import close_http_client

    await close_http_client()


app = FastAPI(
    title="Papyrus",
    description="Web-based print and scan server",
    version="1.6.0",
    lifespan=lifespan,
    openapi_tags=TAGS_METADATA,
    contact={"name": "Papyrus", "url": "https://github.com/metril/papyrus"},
    servers=[{"url": "/", "description": "This server"}],
    generate_unique_id_function=generate_unique_id,
    # Served by the gated routes below instead of FastAPI's public defaults.
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
install_openapi(app)


async def _docs_gate(request: Request, db: AsyncSession = Depends(get_db)) -> None:
    """/docs, /redoc and /openapi.json need a logged-in user (session cookie or
    Bearer) unless the `public_api_docs` setting is on."""
    from app.routers.settings import get_setting

    value = await get_setting(db, "public_api_docs")
    if value is not None and value.strip().lower() in ("true", "1", "yes"):
        return
    await get_current_user(request, db)


@app.get("/openapi.json", include_in_schema=False, dependencies=[Depends(_docs_gate)])
async def _openapi_json() -> JSONResponse:
    return JSONResponse(app.openapi())


@app.get("/docs", include_in_schema=False, dependencies=[Depends(_docs_gate)])
async def _swagger_ui() -> HTMLResponse:
    return get_swagger_ui_html(openapi_url="/openapi.json", title=f"{app.title} - API docs")


@app.get("/redoc", include_in_schema=False, dependencies=[Depends(_docs_gate)])
async def _redoc() -> HTMLResponse:
    return get_redoc_html(openapi_url="/openapi.json", title=f"{app.title} - API docs")


# Global exception handlers: domain PapyrusError -> its status_code + curated
# detail, cups.IPPError -> 503, and a catch-all that logs the traceback and
# returns a generic 500 so internal detail never leaks to clients. All include
# the current request_id.
register_exception_handlers(app)

# Session middleware for OIDC (must be added before CORS). https_only tracks
# PAPYRUS_BASE_URL's scheme so the cookie isn't marked Secure in http:// dev
# deployments (which would make it unsendable) but is over any https:// one;
# max_age/same_site are set explicitly rather than relying on Starlette's
# defaults, which happen to already match these values.
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    https_only=settings.base_url.lower().startswith("https://"),
    max_age=14 * 24 * 3600,
    same_site="lax",
)

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
# Rate limiting sits inside the alias rewrite and RequestID (sees v1 paths + request id).
app.add_middleware(RateLimitMiddleware)
# Legacy /api/* -> /api/v1/* rewrite; added before RequestID so it sits inside it.
app.add_middleware(LegacyAliasMiddleware, route_exists=lambda scope: _route_exists(app, scope))
app.add_middleware(RequestIDMiddleware)

# API routes
V1 = "/api/v1"
app.include_router(system.router, prefix=f"{V1}/system", tags=["system"])
app.include_router(system.ws_router, prefix=f"{V1}/ws", tags=["system"])
app.include_router(auth.router, prefix=f"{V1}/auth", tags=["auth"])
app.include_router(device.router, prefix=f"{V1}/auth/device", tags=["auth"])
app.include_router(printer.router, prefix=f"{V1}/printers/default", tags=["printer"])
app.include_router(jobs.router, prefix=f"{V1}/jobs", tags=["jobs"])
app.include_router(jobs.share_target_router, prefix=f"{V1}/jobs", tags=["jobs"])
# scanner.router carries absolute sub-paths (/scans, /scan-profiles,
# /scanners/default/*, /ws/scans/{id}); the literal /scanners/default routes
# are included before scanners.router's /{scanner_id} routes.
app.include_router(scanner.router, prefix=V1, tags=["scanner"])
app.include_router(copy.router, prefix=f"{V1}/copy", tags=["copy"])
app.include_router(smb.router, prefix=f"{V1}/smb", tags=["smb"])
app.include_router(email.router, prefix=f"{V1}/email", tags=["email"])
app.include_router(cloud.router, prefix=f"{V1}/cloud", tags=["cloud"])
app.include_router(settings_router.router, prefix=f"{V1}/settings", tags=["settings"])
app.include_router(printers.router, prefix=f"{V1}/printers", tags=["printers"])
app.include_router(scanners.router, prefix=f"{V1}/scanners", tags=["scanners"])
app.include_router(admin.router, prefix=f"{V1}/admin", tags=["admin"])
app.include_router(webdav.router, prefix=f"{V1}/webdav", tags=["webdav"])
app.include_router(webhooks.router, prefix=f"{V1}/webhooks", tags=["webhooks"])
app.include_router(kiosks.router, prefix=f"{V1}/kiosks", tags=["kiosks"])
app.include_router(contacts.router, prefix=f"{V1}/contacts", tags=["contacts"])

# eSCL scanner protocol (no /api prefix — clients expect /eSCL/ at root)
app.include_router(escl.router, tags=["escl"], include_in_schema=False)


def resolve_static_file(static_dir: str, path: str) -> str | None:
    """Resolve `path` under `static_dir`, rejecting any path-traversal escape.

    `path` is untrusted, URL-decoded, and unauthenticated (it's whatever the
    caller sent as `/{path:path}` — uvicorn percent-decodes `%2f` etc. before
    routing, so `..%2f..%2f..%2fproc/self/environ` arrives here identical to
    a literal `../../../proc/self/environ`). Resolve it with `os.path.realpath`
    and only return it if the resolved path is actually contained within
    `static_dir` (this also rejects a symlink planted under `static_dir` that
    points outside it) and names a real file. Otherwise return None so the
    caller falls back to index.html.

    A `path` containing an embedded NUL byte (e.g. `%00` — h11/uvicorn accept
    it and Starlette's `path` convertor happily matches it) makes
    `os.path.realpath` raise `ValueError` rather than returning a string; the
    old inline `os.path.isfile` call silently swallowed that (`genericpath.
    isfile` catches `OSError`/`ValueError`), so treat it the same way here —
    not a match, fall back to index.html — instead of letting it propagate
    into an unauthenticated 500.
    """
    try:
        real_static_dir = os.path.realpath(static_dir)
        candidate = os.path.realpath(os.path.join(static_dir, path))
    except (OSError, ValueError):
        return None
    if candidate.startswith(real_static_dir + os.sep) and os.path.isfile(candidate):
        return candidate
    return None


# Serve frontend static files (built React app) with SPA fallback
# All static files (including /assets/*) are served through spa_fallback.
# This avoids StaticFiles returning JSON 404s for stale hashed asset requests.
static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
if os.path.isdir(static_dir):
    @app.get("/{path:path}")
    async def spa_fallback(path: str):
        if path == "api" or path.startswith("api/"):
            # Never serve index.html for an unknown API path.
            raise HTTPException(status_code=404, detail="Not found")
        resolved = resolve_static_file(static_dir, path)
        if resolved:
            return FileResponse(resolved)
        return FileResponse(os.path.join(static_dir, "index.html"))
