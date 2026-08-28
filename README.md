# Papyrus

A web-based print and scan server for network-connected multifunction printers. Provides a responsive web UI for managing print jobs, scanning documents, copying, and integrating with SMB shares, email, and cloud storage.

## Features

- **Print Management**: Upload documents (streamed to disk with early size-limit rejection), hold-release print queue with cached thumbnail previews for held jobs, rich history with preview/selection/bulk delete
- **Document Conversion**: Automatically converts DOCX, ODT, XLSX, PPTX to PDF for printing (via LibreOffice)
- **Scanning**: Initiate scans from the web UI with configurable resolution, color mode, and format
- **Multi-page ADF Scanning**: Batch scan from the automatic document feeder into a single PDF
- **Copy**: One-click scan-then-print workflow
- **SMB Integration**: Browse and print from network shares, save scans to shares
- **Email**: Send scanned documents as email attachments, receive attachments for printing via webhook
- **Cloud Storage**: Browse, download, and print files from Google Drive, Dropbox, OneDrive, and Nextcloud/WebDAV; upload scans to cloud (OAuth2 or basic auth)
- **FTP/SFTP**: Upload scans to FTP, FTPS, or SFTP servers as a delivery target
- **Paperless-ngx**: Send scans directly to Paperless-ngx for document archival and OCR
- **OCR / Searchable PDFs**: Automatic or manual OCR via Tesseract/ocrmypdf; produces searchable PDFs
- **Scan Profiles**: Save and load scan presets (resolution, color, format, source, OCR, post-actions) per user
- **PDF Collation**: Merge multiple scans into a single PDF document
- **Image Enhancement**: Adjust brightness, contrast, rotation, auto-crop, and auto-deskew on scanned images
- **Scan Previews**: Cached thumbnail images for fast-loading scan list/grid previews
- **Template Naming**: Configurable filename templates for delivered scans using variables ({date}, {time}, {id}, etc.)
- **Network Printer**: Appears as an AirPrint/IPP printer on the LAN; network print jobs enter the hold-release queue. A built-in zero-config **Papyrus** printer works out of the box — jobs sent to it are held and routed to your default printer — and each configured printer is also advertised under its own name. All managed queues use CUPS's `abort-job` error policy, so a single failed job is dropped rather than disabling the queue for everyone.
- **Printer Discovery**: mDNS scan pick-list to add printers, IP probe with IPP auto-enrichment (model/location), and a one-page test print to confirm which physical device a configured printer maps to
- **Network Scanner**: Appears as an eSCL/AirScan scanner on the LAN; devices can scan directly via the eSCL protocol. The protocol has no authentication of its own, so requests are restricted to LAN source addresses — behind a same-host reverse proxy, the rightmost `X-Forwarded-For` entry is what's checked
- **Webhooks**: Outgoing HTTP notifications with HMAC-SHA256 signing for print/scan events
- **Printer Status**: Live toner/ink levels and state display from CUPS marker attributes, pushed to clients over WebSocket as changes are detected (no polling)
- **Supply & Error Alerts**: Background poller watches toner/ink levels and printer error/offline state; fires a webhook (`printer.supply_low` / `printer.error`) and an optional email the moment a condition starts, with hysteresis so it doesn't repeat while the condition persists and a quiet (webhook-only) notice when it clears
- **Audit Log**: Tracks print releases, scan completions, deletions, and settings changes (admin view)
- **Usage Dashboard**: Print/scan counts by status, a 30-day activity trend chart, per-user breakdown, and the default printer's supply levels (admin view)
- **Real-time Updates**: WebSocket-based live updates — job/scan events push the full object so the UI applies them incrementally, plus scan progress and eSCL scan toast notifications
- **Release PIN**: Optional PIN-protected print release for secure shared environments — a set PIN also gates viewing, downloading, and reprinting that job's file for anyone but its owner or an admin; only an admin can delete or cancel another user's job
- **Reprint**: Re-submit completed, failed, or cancelled print jobs from history
- **Retention Policies**: Automatic cleanup of old scans and print jobs with configurable retention periods
- **Backup / Restore**: Export and import all application settings as JSON (admin)
- **Detailed Health Check**: System health endpoint with CUPS, scanner, database, disk, and uptime status
- **Structured Logging**: JSON (or plain dev-mode) logs with a per-request `X-Request-ID`, echoed to the client and included in every log line and error response for easy correlation
- **PWA Support**: Installable as a Progressive Web App on mobile and desktop; share files into Papyrus directly from the OS share sheet (Android/Chromium only — iOS Safari ignores the manifest's `share_target`, so iPhone users use the in-app upload flow instead). Shared files are held without a release PIN even when `require_release_pin` is on — the share flow has no way to display a generated PIN
- **Responsive Design**: Works on phones, tablets, and desktops, with light and dark themes; the mobile nav bar shows the most-used sections plus a "More" sheet for the rest
- **Authentication**: OIDC (Authentik/Keycloak) with group-based role mapping, API tokens with fine-grained permissions
- **User Management**: Admin user list with role management, user profile display with logout
- **Login Screen**: Clean SSO login page for unauthenticated users

## Supported Hardware

- Brother DCP-L2540DW (primary target)
- Other CUPS/SANE-compatible printers and scanners may work with configuration

## Tech Stack

- **Backend**: Python 3.12, FastAPI, SQLAlchemy (async), PostgreSQL
- **Frontend**: React 19, TypeScript, Vite, Tailwind CSS, TanStack Query (server state), Zustand (client state), lazy-loaded routes, vitest/Testing Library (unit tests)
- **Design**: "Paper & ink" design system — warm paper-toned grays with a process-cyan ink accent, IBM Plex Sans/Mono type (mono for data readouts), LED status dots and perforation dividers as an office-machine motif, full light/dark parity
- **Printing**: CUPS (driverless/IPP Everywhere), AirPrint via Avahi mDNS, printer discovery via python-zeroconf
- **Scanning**: SANE via scanimage (sane-airscan for WSD), eSCL server for network scanning
- **Deployment**: Docker with multi-stage build (`network_mode: host` for mDNS), behind Traefik

## Quick Start

### Docker (Recommended)

```bash
# Clone the repository
git clone <repo-url> papyrus
cd papyrus

# Copy and edit environment configuration
cp .env.example .env
# Edit .env with your printer IP, OIDC settings, etc.

# Start the application
docker compose -f docker/compose.yaml up --build
```

The web UI will be available at `http://localhost:8080`.

The container supervises its internal daemons: if cupsd or avahi-daemon dies,
the entrypoint exits non-zero and compose's `restart: unless-stopped` brings
the container back automatically (a rising `docker inspect papyrus --format
'{{.RestartCount}}'` is the tell). If AirPrint clients report the printer as
offline, work through [docs/troubleshooting.md](docs/troubleshooting.md)
before restarting the container — a bounce destroys the evidence.

### Development Setup

```bash
# Backend
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload --port 8000

# Frontend (in another terminal)
cd frontend
npm install
npm run dev
```

### Testing

Backend tests are split into two tiers:

- **Unit tests** run with no external services and pass with just `cd backend && pytest`.
- **Integration tests** (auth, settings, jobs, printers, eSCL, webhooks, error handlers — anything that touches the database) need a real Postgres. Start one locally with:

  ```bash
  docker run -d --name papyrus-test-pg \
    -e POSTGRES_USER=papyrus -e POSTGRES_PASSWORD=papyrus -e POSTGRES_DB=papyrus_test \
    -p 127.0.0.1:5433:5432 postgres:16-alpine
  ```

  With the container running, `pytest` picks it up automatically (default `PAPYRUS_TEST_DB_URL=postgresql+asyncpg://papyrus:papyrus@localhost:5433/papyrus_test`, overridable via env) and runs the whole suite against it, applying Alembic migrations itself. Without it, the integration tests skip cleanly with reason "test Postgres unreachable...".

Frontend unit tests use vitest + Testing Library (`cd frontend && npm test`).

### Continuous Integration

`.github/workflows/ci.yml` runs on every push/PR: the backend job (`ruff check` + `pytest`) brings up a `postgres:16-alpine` service container so both tiers of tests run, then fails the job if the JUnit report shows any skipped test — a broken service container can't silently reduce the suite to unit-only. The frontend job runs `eslint` + `npm test` (vitest/Testing Library) + `npm run build`.

## Configuration

Infrastructure settings use environment variables (`PAPYRUS_` prefix). All other settings (SMTP, cloud storage, scanner, printer, OCR, retention, etc.) are configured via the **Settings UI** and stored in the database.

See [.env.example](.env.example) for the full list.

### Required Environment Variables

| Variable | Description |
|----------|-------------|
| `POSTGRES_PASSWORD` | Password for the bundled Postgres container (interpolated into the default `PAPYRUS_DB_URL`) |
| `PAPYRUS_DB_URL` | PostgreSQL connection URL |
| `PAPYRUS_ENCRYPTION_KEY` | Fernet key for encrypting secrets at rest |
| `PAPYRUS_SESSION_SECRET` | Session cookie encryption key |
| `PAPYRUS_BASE_URL` | Public URL (for OIDC callbacks and webhooks) |
| `PAPYRUS_OIDC_ISSUER` | OIDC provider issuer URL |
| `PAPYRUS_OIDC_CLIENT_ID` | OIDC client ID |
| `PAPYRUS_OIDC_CLIENT_SECRET` | OIDC client secret |

`POSTGRES_PASSWORD` and `PAPYRUS_SESSION_SECRET` need real values, not placeholders — `docker/compose.yaml` refuses to start the containers without them, and the app itself hard-fails at startup on a blank or default session secret even outside Docker (downgraded to a warning only when `PAPYRUS_DEV_MODE` is set). Generate all three secrets with the commands already in [.env.example](.env.example): `openssl rand -hex 32` for `POSTGRES_PASSWORD` and `PAPYRUS_SESSION_SECRET`, and the `cryptography` one-liner there for `PAPYRUS_ENCRYPTION_KEY`. Once you're running behind TLS, set `PAPYRUS_BASE_URL` to an `https://` URL — the session cookie is only marked `Secure` when the base URL starts with `https://`.

**Upgrading an existing deployment**: Postgres only applies `POSTGRES_PASSWORD` when it initializes a fresh data volume — it has no effect against a volume that's already been through `docker compose up` at least once. If your `papyrus` role was created before `POSTGRES_PASSWORD` was required here, its password is still whatever it was then (the old default was literally `secret`), and simply setting `POSTGRES_PASSWORD` to a new value will not change it — `PAPYRUS_DB_URL` will fail auth on every restart from then on. Before changing `POSTGRES_PASSWORD` on an existing deployment, either set it to the value already baked into the volume (`secret`, if you never set it explicitly before), or connect to the running database first and change the role's password to match: `docker compose exec db psql -U papyrus -c "ALTER USER papyrus WITH PASSWORD '<new-password>'"`, then set `POSTGRES_PASSWORD` to that same value.

### Settings UI

After first login, configure everything else via **Settings**:
- **Printers**: Add/manage CUPS printers — discover via mDNS pick-list (`GET /api/printers/discover`), probe an IP with IPP enrichment (`GET /api/printers/probe`), refresh a configured printer's device info (`POST /api/printers/{id}/refresh-info`), or print a test page (`POST /api/printers/{id}/test-page`)
- **Scanners**: Register Brother scanners (brscan4), probe by IP, or discover via mDNS
- **OIDC**: Admin group mapping, groups claim name, scopes
- **Email**: SMTP, webhook secret
- **Cloud**: Google Drive, Dropbox, OneDrive OAuth credentials
- **OCR**: Enable/disable, language
- **FTP/SFTP**: Upload targets
- **Paperless-ngx**: URL and API token
- **Retention**: Scan and print job cleanup periods
- **Alerts**: Enable supply/error alerts, toner threshold percentage, notification email, and poll interval

## License

TBD
