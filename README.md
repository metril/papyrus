# Papyrus

A web-based print and scan server for network-connected multifunction printers. Provides a responsive web UI for managing print jobs, scanning documents, copying, and integrating with SMB shares, email, and cloud storage.

## Features

- **Print Management**: Upload documents, hold-release print queue, rich history with preview/selection/bulk delete
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
- **Template Naming**: Configurable filename templates for delivered scans using variables ({date}, {time}, {id}, etc.)
- **Network Printer**: Appears as an AirPrint/IPP printer on the LAN; network print jobs enter the hold-release queue
- **Network Scanner**: Appears as an eSCL/AirScan scanner on the LAN; devices can scan directly via the eSCL protocol
- **Webhooks**: Outgoing HTTP notifications with HMAC-SHA256 signing for print/scan events
- **Printer Status**: Live toner/ink levels and state display from CUPS marker attributes
- **Audit Log**: Tracks print releases, scan completions, deletions, and settings changes (admin view)
- **Usage Dashboard**: Print/scan counts by status, daily activity charts (admin view)
- **Real-time Updates**: WebSocket-based live job status, scan progress, and eSCL scan toast notifications
- **PWA Support**: Installable as a Progressive Web App on mobile and desktop
- **Responsive Design**: Works on phones, tablets, and desktops
- **Authentication**: OIDC (Authentik/Keycloak) for users, API tokens for programmatic access

## Supported Hardware

- Brother DCP-L2540DW (primary target)
- Other CUPS/SANE-compatible printers and scanners may work with configuration

## Tech Stack

- **Backend**: Python 3.12, FastAPI, SQLAlchemy (async), PostgreSQL
- **Frontend**: React 18, TypeScript, Vite, Tailwind CSS
- **Printing**: CUPS (driverless/IPP Everywhere), AirPrint via Avahi mDNS
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

## Configuration

All configuration is via environment variables with the `PAPYRUS_` prefix. See [.env.example](.env.example) for all available options.

| Variable | Description | Required |
|----------|-------------|----------|
| `PAPYRUS_DB_URL` | PostgreSQL connection URL | Yes |
| `PAPYRUS_OIDC_ISSUER` | OIDC provider issuer URL | Yes |
| `PAPYRUS_OIDC_CLIENT_ID` | OIDC client ID | Yes |
| `PAPYRUS_OIDC_CLIENT_SECRET` | OIDC client secret | Yes |
| `PAPYRUS_PRINTER_URI` | Printer IPP URI (e.g., `ipp://192.168.1.100/ipp`) | Yes |
| `PAPYRUS_SCANNER_DEVICE` | SANE scanner device string | Yes |
| `PAPYRUS_ENCRYPTION_KEY` | Fernet key for encrypting secrets at rest | Yes |
| `PAPYRUS_BASE_URL` | Public URL for OAuth callbacks | Yes |

## License

TBD
