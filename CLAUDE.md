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
- **Auth**: OIDC (Authentik/Keycloak) via authlib + admin-generated API tokens
- **SMB**: pysmb for network share browsing/read/write
- **WebDAV**: httpx-based WebDAV client for Nextcloud and other WebDAV servers
- **FTP/SFTP**: stdlib ftplib + optional paramiko for file uploads
- **Image Processing**: Pillow + numpy for brightness/contrast/rotation/crop/deskew
- **PWA**: vite-plugin-pwa with workbox service worker
- **Deploy**: Docker multi-stage build (`network_mode: host` for mDNS) behind Traefik

## Project Structure
- `backend/app/` — FastAPI application
  - `main.py` — App factory, lifespan, middleware
  - `config.py` — Pydantic Settings (`PAPYRUS_` env prefix)
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
All backend config uses `PAPYRUS_` prefix. See `backend/app/config.py` for full list.
Key vars: `PAPYRUS_DB_URL`, `PAPYRUS_OIDC_ISSUER`, `PAPYRUS_OIDC_CLIENT_ID`, `PAPYRUS_OIDC_CLIENT_SECRET`, `PAPYRUS_PRINTER_URI`, `PAPYRUS_SCANNER_DEVICE`, `PAPYRUS_ENCRYPTION_KEY`, `PAPYRUS_BASE_URL`
Cloud OAuth: `PAPYRUS_GDRIVE_CLIENT_ID`, `PAPYRUS_GDRIVE_CLIENT_SECRET`, `PAPYRUS_DROPBOX_APP_KEY`, `PAPYRUS_DROPBOX_APP_SECRET`, `PAPYRUS_ONEDRIVE_CLIENT_ID`, `PAPYRUS_ONEDRIVE_CLIENT_SECRET`
Paperless-ngx: `PAPYRUS_PAPERLESS_URL`, `PAPYRUS_PAPERLESS_API_TOKEN`
OCR: `PAPYRUS_OCR_ENABLED`, `PAPYRUS_OCR_LANGUAGE`
FTP/SFTP: `PAPYRUS_FTP_HOST`, `PAPYRUS_FTP_PORT`, `PAPYRUS_FTP_USERNAME`, `PAPYRUS_FTP_PASSWORD`, `PAPYRUS_FTP_REMOTE_DIR`, `PAPYRUS_FTP_PROTOCOL`
Scan naming: `PAPYRUS_SCAN_FILENAME_TEMPLATE`
Print: `PAPYRUS_REQUIRE_RELEASE_PIN`, `PAPYRUS_PRINT_RETENTION_DAYS`
Email webhook: `PAPYRUS_EMAIL_WEBHOOK_SECRET`, `PAPYRUS_EMAIL_WEBHOOK_RATE_LIMIT`
Network: `PAPYRUS_NETWORK_PRINTER_ENABLED`, `PAPYRUS_NETWORK_PRINTER_NAME`, `PAPYRUS_ESCL_ENABLED`

## Development Rules
- **Commits**: Commit frequently as work progresses. Never mention AI assistants in commit messages.
- **README.md**: Keep updated with any significant changes to setup, features, or architecture.
- **CLAUDE.md**: Keep updated with any changes to project structure, commands, or conventions.
- **Security**: Never use `shell=True` in subprocess calls. Always use argument lists. Validate file uploads server-side. Encrypt sensitive data at rest (Fernet).
- **Subprocess scanning**: Use `scanimage` with explicit argument lists, never string interpolation.
- **Database**: Use async SQLAlchemy with asyncpg. All migrations via Alembic.
- **Frontend**: TypeScript strict mode. Tailwind for styling. Zustand for state.
