# Papyrus - Development Guide

## Project Overview
Papyrus is a web-based print and scan server for network-connected Brother DCP-L2540DW (and potentially other devices). It provides a responsive web UI for managing print jobs (hold-release with optional PIN, reprint), scanning (with OCR, profiles, PDF collation, image enhancement, deskew, and template naming), copying, SMB share integration, bidirectional email (send + webhook receive), bidirectional cloud storage (Google Drive/Dropbox/OneDrive/Nextcloud via OAuth2 or WebDAV), FTP/SFTP upload, Paperless-ngx archival, outgoing webhooks, audit logging, usage dashboard, backup/restore, retention policies, and PWA support.

## Tech Stack
- **Backend**: Python 3.12, FastAPI, Uvicorn, SQLAlchemy async (asyncpg), Alembic
- **Frontend**: React 18, Vite, TypeScript, Tailwind CSS, Zustand, React Router v6
- **Database**: PostgreSQL 16
- **Printing**: CUPS (driverless/IPP Everywhere), AirPrint via Avahi mDNS, pycups
- **Scanning**: `scanimage` subprocess (SANE/sane-airscan), eSCL server for network scanning
- **Network Discovery**: Avahi mDNS (AirPrint `_ipp._tcp`, eSCL `_uscan._tcp`)
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
  - `services/` — Business logic (CUPS, scanning, SMB, email, cloud, Paperless-ngx, OCR, audit, WebDAV, FTP, image enhancement, webhooks, retention)
  - `models.py` — SQLAlchemy ORM models
  - `schemas.py` — Pydantic request/response models
  - `database.py` — Async engine (asyncpg)
- `frontend/src/` — React application
  - `api/` — Typed API client + WebSocket hook
  - `components/` — UI components organized by feature
  - `hooks/` — Custom React hooks
  - `pages/` — Route pages
  - `store/` — Zustand state stores
- `docker/` — Dockerfile, compose.yaml, entrypoint, CUPS/SANE/Avahi configs
  - `cups/papyrus-backend` — Custom CUPS backend script for network print → hold queue
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
```

## Environment Variables
Only infrastructure settings use env vars (`PAPYRUS_` prefix). All other settings are managed via the Settings UI and stored in the AppConfig database table.

**Infrastructure env vars** (see `backend/app/config.py`):
- `PAPYRUS_DB_URL` — PostgreSQL connection URL
- `PAPYRUS_ENCRYPTION_KEY` — Fernet key for encrypting secrets at rest
- `PAPYRUS_SESSION_SECRET` — Session cookie encryption key
- `PAPYRUS_BASE_URL` — Public URL for OIDC callbacks and webhooks
- `PAPYRUS_OIDC_ISSUER`, `PAPYRUS_OIDC_CLIENT_ID`, `PAPYRUS_OIDC_CLIENT_SECRET` — OIDC provider
- `PAPYRUS_DEV_MODE` — Development mode (bypass OIDC)

**UI-managed settings** (stored in DB, configured via Settings page):
SMTP, cloud OAuth, scanner/printer config, OCR, FTP/SFTP, Paperless-ngx, retention, webhooks, OIDC group mapping, etc. Use `get_setting(db, key)` from `app.routers.settings` to read them in code.

## Development Rules
- **Commits**: Commit frequently as work progresses. Never mention AI assistants in commit messages.
- **README.md**: Keep updated with any significant changes to setup, features, or architecture.
- **CLAUDE.md**: Keep updated with any changes to project structure, commands, or conventions.
- **Security**: Never use `shell=True` in subprocess calls. Always use argument lists. Validate file uploads server-side. Encrypt sensitive data at rest (Fernet).
- **Subprocess scanning**: Use `scanimage` with explicit argument lists, never string interpolation.
- **Database**: Use async SQLAlchemy with asyncpg. All migrations via Alembic.
- **Frontend**: TypeScript strict mode. Tailwind for styling. Zustand for state.
