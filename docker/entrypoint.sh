#!/bin/bash
set -e

# On SIGTERM (docker stop) forward the signal to cupsd, avahi-daemon, and
# uvicorn so they all get a chance to shut down cleanly instead of being
# SIGKILLed at the container's stop grace deadline -- a hard kill of cupsd
# mid-write risks a corrupt /var/spool/cups job (F102). `init: true` in
# compose.yaml (docker's built-in tini) handles reaping any orphaned
# children; this trap handles telling the daemons to actually stop. uvicorn
# is deliberately NOT exec'd below so this shell stays alive to run this
# trap for the container's whole life -- exec'ing it would replace this
# process image and silently drop the trap.
trap 'kill -TERM $CUPS_PID $AVAHI_PID $UVICORN_PID 2>/dev/null' TERM

# Start Avahi mDNS daemon for network discovery (AirPrint + eSCL scanner).
# Foreground (backgrounded here, not --daemonize) so this script owns its
# PID directly instead of losing track of it behind avahi's own fork.
mkdir -p /run/avahi-daemon
avahi-daemon --no-chroot --no-drop-root &
AVAHI_PID=$!

# Start CUPS daemon the same way -- foreground (`-f`), backgrounded by this
# shell -- so $CUPS_PID is the real cupsd process, not an untracked daemon.
cupsd -f &
CUPS_PID=$!

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

# Start the application. Backgrounded (not exec'd, see the trap comment
# above) so this shell survives to catch SIGTERM and forward it to cupsd
# and avahi-daemon too.
uvicorn app.main:app --host 0.0.0.0 --port 8080 &
UVICORN_PID=$!
wait $UVICORN_PID
