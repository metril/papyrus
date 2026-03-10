# Papyrus - Development Guide

## Project Overview
Papyrus is a web-based print and scan server for network-connected Brother DCP-L2540DW (and potentially other devices). It provides a responsive web UI for managing print jobs (hold-release), scanning, copying, SMB share integration, bidirectional email (send + webhook receive), and bidirectional cloud storage (Google Drive/Dropbox with OAuth2 browse/download/upload).

## Tech Stack
- **Backend**: Python 3.12, FastAPI, Uvicorn, SQLAlchemy async (asyncpg), Alembic
- **Frontend**: React 18, Vite, TypeScript, Tailwind CSS, Zustand, React Router v6
- **Database**: PostgreSQL 16
- **Printing**: CUPS + brlaser driver, wrapped by pycups
- **Scanning**: `scanimage` subprocess (SANE/sane-airscan)
- **Doc Conversion**: LibreOffice headless (DOCX/ODT/XLSX/PPTX → PDF)
- **Auth**: OIDC (Authentik/Keycloak) via authlib + admin-generated API tokens
- **SMB**: pysmb for network share browsing/read/write
- **Deploy**: Docker multi-stage build behind Traefik

## Project Structure
- `backend/app/` — FastAPI application
  - `main.py` — App factory, lifespan, middleware
  - `config.py` — Pydantic Settings (`PAPYRUS_` env prefix)
  - `auth/` — OIDC + API token auth
  - `routers/` — API route handlers
  - `services/` — Business logic (CUPS, scanning, SMB, email, cloud)
  - `models.py` — SQLAlchemy ORM models
  - `schemas.py` — Pydantic request/response models
  - `database.py` — Async engine (asyncpg)
- `frontend/src/` — React application
  - `api/` — Typed API client + WebSocket hook
  - `components/` — UI components organized by feature
  - `hooks/` — Custom React hooks
  - `pages/` — Route pages
  - `store/` — Zustand state stores
- `docker/` — Dockerfile, compose.yaml, entrypoint, CUPS/SANE configs

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
Cloud OAuth: `PAPYRUS_GDRIVE_CLIENT_ID`, `PAPYRUS_GDRIVE_CLIENT_SECRET`, `PAPYRUS_DROPBOX_APP_KEY`, `PAPYRUS_DROPBOX_APP_SECRET`
Email webhook: `PAPYRUS_EMAIL_WEBHOOK_SECRET`, `PAPYRUS_EMAIL_WEBHOOK_RATE_LIMIT`

## Development Rules
- **Commits**: Commit frequently as work progresses. Never mention AI assistants in commit messages.
- **README.md**: Keep updated with any significant changes to setup, features, or architecture.
- **CLAUDE.md**: Keep updated with any changes to project structure, commands, or conventions.
- **Security**: Never use `shell=True` in subprocess calls. Always use argument lists. Validate file uploads server-side. Encrypt sensitive data at rest (Fernet).
- **Subprocess scanning**: Use `scanimage` with explicit argument lists, never string interpolation.
- **Database**: Use async SQLAlchemy with asyncpg. All migrations via Alembic.
- **Frontend**: TypeScript strict mode. Tailwind for styling. Zustand for state.
