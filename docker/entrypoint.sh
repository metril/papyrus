#!/bin/bash
set -e

# Start Avahi mDNS daemon for network discovery (AirPrint + eSCL scanner)
mkdir -p /run/avahi-daemon
avahi-daemon --daemonize --no-chroot --no-drop-root || echo "Warning: avahi-daemon failed to start"

# Start CUPS daemon
cupsd

# Wait for CUPS to be ready
sleep 2

# Create data directories
mkdir -p "${PAPYRUS_SCAN_DIR:-/app/data/scans}" "${PAPYRUS_UPLOAD_DIR:-/app/data/uploads}"

# Run database migrations
cd /app/backend
python -m alembic upgrade head

# Start the application
exec uvicorn app.main:app --host 0.0.0.0 --port 8080
