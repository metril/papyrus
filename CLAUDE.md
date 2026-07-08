# Papyrus - Development Guide

## Project Overview
Papyrus is a web-based print and scan server for network-connected Brother DCP-L2540DW (and potentially other devices). It provides a responsive web UI for managing print jobs (hold-release with optional PIN, reprint), scanning (with OCR, profiles, PDF collation, image enhancement, deskew, and template naming), copying, SMB share integration, bidirectional email (send + webhook receive), bidirectional cloud storage (Google Drive/Dropbox/OneDrive/Nextcloud via OAuth2 or WebDAV), FTP/SFTP upload, Paperless-ngx archival, outgoing webhooks, audit logging, usage dashboard, backup/restore, retention policies, and PWA support.

## Tech Stack
- **Backend**: Python 3.12, FastAPI, Uvicorn, SQLAlchemy async (asyncpg), Alembic
- **Frontend**: React 19, Vite, TypeScript (strict, `verbatimModuleSyntax`), Tailwind CSS, TanStack Query v5 (server state), Zustand (client state), React Router v6, vitest + Testing Library + msw (unit tests). Design system (see `frontend/src/index.css` `@theme`): "paper" = remapped gray-* scale + "ink" accent scale (use `ink-*`, never `blue-*`); IBM Plex Sans/Mono via @fontsource with mono reserved for data readouts (IDs, timestamps, sizes, URLs/IPs/device strings); `rule-perf` (perforation divider) and `led` (status dot) utilities; Skeleton/EmptyState/ErrorState primitives in `components/common/`; lucide-react icons
- **Database**: PostgreSQL 16
- **Printing**: CUPS (driverless/IPP Everywhere), AirPrint via Avahi mDNS, pycups
- **Scanning**: `scanimage` subprocess (SANE/sane-airscan), eSCL server for network scanning
- **Network Discovery**: Avahi mDNS (AirPrint `_ipp._tcp`, eSCL `_uscan._tcp`); python-zeroconf for admin-triggered printer discovery scans (`_ipp`/`_ipps`/`_printer._tcp`) since avahi-browse/CUPS dnssd need D-Bus, unavailable in-container
- **Doc Conversion**: LibreOffice headless (DOCX/ODT/XLSX/PPTX → PDF)
- **OCR**: Tesseract + ocrmypdf for searchable PDFs
- **Auth**: OIDC (Authentik/Keycloak) via authlib + admin-generated API tokens + group-based role mapping
- **SMB**: pysmb for network share browsing/read/write
- **WebDAV**: httpx-based WebDAV client for Nextcloud and other WebDAV servers
- **FTP/SFTP**: stdlib ftplib + optional paramiko for file uploads
- **Image Processing**: Pillow + numpy for brightness/contrast/rotation/crop/deskew
- **PWA**: vite-plugin-pwa with workbox service worker
- **Deploy**: Docker multi-stage build (`network_mode: host` for mDNS) behind Traefik

## Project Structure
- `backend/app/` — FastAPI application
  - `main.py` — App factory, lifespan, middleware
  - `config.py` — Infrastructure-only Pydantic Settings (`PAPYRUS_` env prefix); UI-managed settings are in AppConfig DB
  - `auth/` — OIDC + API token auth
  - `routers/` — API route handlers
  - `services/` — Business logic (CUPS, scanning, SMB, email, cloud, Paperless-ngx, OCR, audit, WebDAV, FTP, image enhancement, webhooks, retention, `thumbnail_service.py` for cached scan previews, `settings_cache.py` for a 30s in-process TTL cache in front of `get_setting`, `http_client.py` for a shared pooled `httpx.AsyncClient`, `discovery_service.py` for zeroconf mDNS printer discovery, `ipp_client.py` for a minimal hand-rolled IPP Get-Printer-Attributes client (probe/discover enrichment, reused by supply alerts), `test_page_service.py` for printer identify-sheet test pages, `alert_service.py` for supply/error alerts — `check_alerts(db)` polled by `main._alert_loop`, with False→True hysteresis persisted in the internal `alert_state` AppConfig JSON row and fan-out to `printer.supply_low`/`printer.error` webhooks + `alert_email`)
  - `models.py` — SQLAlchemy ORM models (`Printer.make_and_model`/`Printer.location`, nullable, populated via IPP probing — migration 013)
  - `schemas.py` — Pydantic request/response models
  - `database.py` — Async engine (asyncpg)
- `frontend/src/` — React application
  - `api/` — Typed API client + WebSocket hook; `queries.ts` holds the `queryKeys` cache-key factory and every TanStack Query hook (single source of truth for query keys — never inline one at a call site); `queryClient.ts` builds the shared `QueryClient` and wires global error-toast handling for failed queries/mutations
  - `components/` — UI components organized by feature; row-rendering list components (`print/JobRow.tsx`, `scan/ScanRow.tsx`, `history/HistoryRow.tsx`) are `React.memo`'d with stable `useCallback` handlers from their parent list
  - `hooks/` — Custom React hooks, incl. `useRealtimeBridge.ts` — the WebSocket→Query-cache bridge, the sole realtime path (mounted once in AppShell; applies events via `queryClient.setQueryData`, never a refetch)
  - `pages/` — Route pages, lazy-loaded (`React.lazy` + `Suspense`, see `AppShell`) and code-split per route
  - `store/` — Zustand stores, client state only (server state lives in the Query cache): `authStore.ts`, `themeStore.ts`, `toastStore.ts` (also usable from non-React code via `showToast`), `connectionStore.ts` (per-channel WS connected flags the bridge writes and `usePrinterStatus`'s poll fallback reads), and a slimmed `scanStore.ts` (transient scan-progress UI state only)
- `docker/` — Dockerfile, compose.yaml, entrypoint, CUPS/SANE/Avahi configs
  - `cups/papyrus-backend` — Custom CUPS backend script for network print → hold queue. On a non-2xx from the ingest API it now `exit 1`s (`CUPS_BACKEND_FAILED`) so failures surface as failed jobs instead of silently completing — safe only because every Papyrus-created queue uses `printer-error-policy=abort-job` (aborts the one job, never disables the queue). Keeps the temp `.pdf` under `PAPYRUS_UPLOAD_DIR` for debugging.
  - `avahi/airprint.service` — **static** AirPrint advert baked into `/etc/avahi/services/`; hardcodes `rp=printers/Papyrus`, so it needs a CUPS queue literally named `Papyrus`. That built-in zero-config hold queue is created at startup by `cups_admin.ensure_default_queue()` (called from `main._reconcile_on_startup`, since `printers.conf` isn't persisted) with **no** second Avahi advert. Jobs to it are held and, having no matching `Printer` row, route to the **default printer** at ingest and release normally. The name `Papyrus` must stay in sync across `airprint.service`, `cups_admin.DEFAULT_QUEUE_NAME`, and `printers._SELF_ADVERTISEMENT_RESOURCE_MARKER`. Per-printer queues added via the UI get their own dynamic `{cups_name}.service` advert.
  - `avahi/` — Avahi mDNS daemon config + eSCL service advertisement

## Key Commands
```bash
# Backend development
cd backend && pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8000

# Frontend development
cd frontend && npm install
npm run dev

# Database migrations
cd backend && alembic upgrade head
cd backend && alembic revision --autogenerate -m "description"

# Docker
docker compose -f docker/compose.yaml up --build
docker compose -f docker/compose.yaml down

# Tests
cd backend && pytest
cd frontend && npm test

# Backend integration tests need a local test Postgres; they auto-skip
# (reason "test Postgres unreachable…") if it isn't running:
docker run -d --name papyrus-test-pg -e POSTGRES_USER=papyrus -e POSTGRES_PASSWORD=papyrus -e POSTGRES_DB=papyrus_test -p 127.0.0.1:5433:5432 postgres:16-alpine

# React Compiler experiment (default OFF; see vite.config.ts)
cd frontend && REACT_COMPILER=1 npm run build
```

The local backend venv doesn't have pycups (host lacks CUPS headers); `pip install -e ".[dev]"` still works and `backend/tests/conftest.py` stubs `sys.modules["cups"]` so tests import cleanly without it. Docker/CI images have real pycups. CI (`.github/workflows/ci.yml`) runs backend ruff+pytest and frontend eslint+`npm test` (vitest)+build on every push/PR. The backend job also runs a `postgres:16-alpine` service container (port 5433→5432, matching `PAPYRUS_TEST_DB_URL`'s default) so the ~52 integration tests execute in CI instead of skipping; a step after Pytest parses the `--junitxml` report and fails the job if any test was skipped, so a broken service container can't silently downgrade the suite.

## Environment Variables
Only infrastructure settings use env vars (`PAPYRUS_` prefix). All other settings are managed via the Settings UI and stored in the AppConfig database table.

**Infrastructure env vars** (see `backend/app/config.py`):
- `PAPYRUS_DB_URL` — PostgreSQL connection URL
- `PAPYRUS_ENCRYPTION_KEY` — Fernet key for encrypting secrets at rest
- `PAPYRUS_SESSION_SECRET` — Session cookie encryption key
- `PAPYRUS_BASE_URL` — Public URL for OIDC callbacks and webhooks
- `PAPYRUS_OIDC_ISSUER`, `PAPYRUS_OIDC_CLIENT_ID`, `PAPYRUS_OIDC_CLIENT_SECRET` — OIDC provider
- `PAPYRUS_OIDC_SCOPES` — OIDC scopes (default: `openid email profile`)
- `PAPYRUS_OIDC_ADMIN_GROUP` — OIDC group name that grants admin role
- `PAPYRUS_OIDC_GROUPS_CLAIM` — Claim name containing group list (default: `groups`)
- `PAPYRUS_DEV_MODE` — Development mode (bypass OIDC)
- `PAPYRUS_CORS_ORIGINS` — Comma-separated list of allowed CORS origins; empty (default) means same-origin only and `CORSMiddleware` isn't added at all, since the app is normally served from behind a reverse proxy on the same origin
- `PAPYRUS_TEST_DB_URL` — Test-only: overrides the Postgres URL the test harness (`backend/tests/conftest.py`) targets (default `postgresql+asyncpg://papyrus:papyrus@localhost:5433/papyrus_test`); set by CI, or export it locally if your test Postgres runs somewhere other than the `papyrus-test-pg` one-liner above

**UI-managed settings** (stored in DB, configured via Settings page):
SMTP, cloud OAuth, scanner/printer config, OCR, FTP/SFTP, Paperless-ngx, retention, webhooks, OIDC group mapping, etc. Use `get_setting(db, key)` from `app.routers.settings` to read them in code. Supply/error alerts add: `alerts_enabled` (bool, default false), `alert_toner_threshold` (int percent, default 20 — markers strictly below this with a *known* level fire `supply_low`; unknown/absent levels never alert), `alert_email` (optional, onset-only — recovery notices are webhook-only), `alert_poll_minutes` (default 5, re-read every cycle by `main._alert_loop`).

**Webhook events** (`app/services/webhook_service.py`'s `WEBHOOK_EVENTS`): `print.release`, `print.delete`, `print.upload`, `print.held` (a job lands in the hold queue — dispatched from the shared upload helper for both `POST /upload` and `POST /api/share-target`, and from network ingest when `auto_release` is off), `print.test_page`, `scan.complete`, `scan.delete`, `settings.update`, `printer.supply_low`, `printer.error` (also fired for offline/stopped printers) — the last two are alert_service's onset/resolved notifications.

## Development Rules
- **Commits**: Commit frequently as work progresses. Never mention AI assistants in commit messages.
- **README.md**: Keep updated with any significant changes to setup, features, or architecture.
- **CLAUDE.md**: Keep updated with any changes to project structure, commands, or conventions.
- **Security**: Never use `shell=True` in subprocess calls. Always use argument lists. Validate file uploads server-side. Encrypt sensitive data at rest (Fernet).
- **Subprocess scanning**: Use `scanimage` with explicit argument lists, never string interpolation.
- **Database**: Use async SQLAlchemy with asyncpg. All migrations via Alembic.
- **Frontend**: TypeScript strict mode. Tailwind for styling. Server state lives in the TanStack Query cache, always keyed via the `queryKeys` factory in `api/queries.ts` (never an inline key array). Zustand is for client-only UI state (auth, theme, toasts, WS connection flags) — never server data. Realtime WebSocket events are applied to the Query cache exclusively through `useRealtimeBridge`'s `setQueryData` calls; components never open their own sockets for list data or refetch in response to an event.
- **Chart colors**: Dashboard charts (`components/dashboard/`) key color to series identity, never position — print is always `--chart-print` (process cyan), scan is always `--chart-scan` (process magenta), defined in `index.css` and flipped for dark mode there; never hardcode a hex in a chart component. HTML marks use `var(--chart-print|scan)` directly; SVG marks (Recharts `stroke`/`fill`) can't resolve `var()`, so they go through `useChartColors()` in `chartTheme.ts`, which re-reads the computed style whenever `<html>`'s `.dark` class flips.
- **Uploads**: Stream to disk (`save_upload_streaming`) rather than buffering whole files in memory; enforce `max_upload_size_mb` with an early 413 while streaming. The upload pipeline (validate mime type, stream-save, PIN handling, held/auto-print dispatch, `print.held` webhook) is factored into `_create_print_job_from_upload` in `app/routers/jobs.py`, shared by `POST /upload` and the PWA share-target route below rather than duplicated.
- **PWA share-target**: `POST /api/share-target` (mounted at `/api`, not `/api/jobs` — see `jobs.share_target_router` in `main.py`) is the manifest's `share_target` action; the OS share sheet navigates the browser there directly (Android/Chromium only), so it deliberately breaks the 401-JSON contract below — an unauthenticated request gets `RedirectResponse("/api/auth/login", 303)` instead. It resolves the user by calling `get_current_user` directly and catching its `HTTPException` (not via `Depends`, so `app.dependency_overrides` doesn't reach it — tests authenticate with a real Bearer token instead), accepts one or more `file` form fields, runs each through the shared upload helper above, and redirects to `/print` (303) on success.
- **WebSocket events**: The `jobs`/`scans`/`printers` channels (`/ws/jobs`, `/ws/scans`, `/ws/printers`) always broadcast the full serialized object (`job_created`/`job_updated`/`job_deleted`, `scan_completed`/`scan_deleted`, `printer_status`), never a partial payload — the frontend applies events incrementally instead of refetching. If a row is gone by the time a background task finishes, skip the broadcast rather than send a partial object.
- **Errors**: Raise domain errors from `app/exceptions.py` (`PapyrusError` subclasses — `NotFoundError` 404, `PrinterUnavailableError`/`ScannerBusyError` 503, `ExternalServiceError` 502, `UploadTooLargeError` 413) instead of `HTTPException`; their `detail` is sent to the client verbatim, so write it for end users and never put internal state, stack traces, or upstream error strings into it. Never do `HTTPException(500, str(e))` — let an unexpected `Exception` fall through to the catch-all handler in `register_exception_handlers`, which logs the traceback and returns a generic message instead.
- **Logging & request IDs**: All app and uvicorn logs flow through `app/logging_config.py`'s single stderr handler (JSON in prod, human-readable in dev) via stdlib `logging.config.dictConfig` — no structlog. `RequestIDMiddleware` (`app/middleware.py`) assigns/echoes an `X-Request-ID` header per request and stores it in the `request_id_var` contextvar (`app/request_context.py`); `get_request_id()` reads it from anywhere (including log filters) and every error JSON body includes the same id under `request_id`.
- **Status-code contract with the frontend**: 401 → the axios interceptor in `frontend/src/api/client.ts` redirects to `/api/auth/login`; keep issuing 401 (never 403) for "not authenticated". 403 → authenticated but lacking the required role/permission (`require_admin`/`require_permission`); the frontend does not redirect on it, so don't reuse 401 for this case. 413 → upload exceeds `max_upload_size_mb`, raised as `UploadTooLargeError` mid-stream by `save_upload_streaming` before the whole body is buffered.
