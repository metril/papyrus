#!/bin/bash
set -e

# Start Avahi mDNS daemon for network discovery (AirPrint + eSCL scanner)
mkdir -p /run/avahi-daemon
avahi-daemon --daemonize --no-chroot --no-drop-root || echo "Warning: avahi-daemon failed to start"

# Start CUPS daemon
cupsd

# Generate a per-container shared secret for the internal network-ingest
# endpoint (F7) and hand it to both sides: the app reads it from the
# environment, and the papyrus CUPS backend script reads it from this file at
# request time (it runs as a separate process CUPS invokes, not a child of
# this shell).
export PAPYRUS_INGEST_TOKEN=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')
mkdir -p /run/papyrus && printf '%s' "$PAPYRUS_INGEST_TOKEN" > /run/papyrus/ingest.token && chmod 600 /run/papyrus/ingest.token

# Wait for CUPS to be ready
sleep 2

# Create data directories
mkdir -p "${PAPYRUS_SCAN_DIR:-/app/data/scans}" "${PAPYRUS_UPLOAD_DIR:-/app/data/uploads}"

# Run database migrations
cd /app/backend
python -m alembic upgrade head

# Start the application
exec uvicorn app.main:app --host 0.0.0.0 --port 8080
