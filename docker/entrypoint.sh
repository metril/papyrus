#!/bin/bash
set -e

# On SIGTERM/SIGINT (docker stop) forward the signal to cupsd, avahi-daemon,
# and uvicorn, then WAIT for each to actually exit before this shell does --
# `init: true` in compose.yaml (docker's built-in tini) makes this shell
# tini's only tracked child, so the instant it exits the kernel SIGKILLs
# everything left in the container's PID namespace. A trap that only
# *signals* the children (never waits for them) would let this shell reach
# its last line and exit within milliseconds of signalling, SIGKILLing
# cupsd/avahi mid-shutdown -- the exact spool-corruption window F102 exists
# to close -- and cutting uvicorn's shutdown grace short instead of
# lengthening it. uvicorn is deliberately NOT exec'd below so this shell
# stays alive for the container's whole life to do this waiting -- exec'ing
# it would replace this process image and silently drop the trap.
SHUTTING_DOWN=0
UVICORN_PID=""
CUPS_PID=""
AVAHI_PID=""
WAIT_RC=0

# Sets the global WAIT_RC to $1's real exit status, safe under `set -e`
# throughout. A single `wait "$pid"`, when interrupted mid-wait by a
# trapped signal, returns immediately with a signal-based status (>128)
# *without* reaping the child -- it is still running. Looping until
# `kill -0` confirms the process is actually gone guarantees the `wait` we
# finally act on is one that blocked with no signal in flight to interrupt
# it, i.e. the child's real exit status. Always `return 0` itself (the
# real status lives in WAIT_RC) so a call to this function is never at risk
# of `set -e` aborting the script on the awaited process's own non-zero
# exit code.
wait_for_pid() {
    local pid="$1"
    WAIT_RC=0
    while true; do
        if wait "$pid" 2>/dev/null; then
            WAIT_RC=0
        else
            WAIT_RC=$?
        fi
        kill -0 "$pid" 2>/dev/null || break
    done
    return 0
}

trap 'SHUTTING_DOWN=1; kill -TERM $UVICORN_PID $CUPS_PID $AVAHI_PID 2>/dev/null || true' TERM INT

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

# A signal received during any step above is deferred by bash until that
# foreground command returns control here (the trap already set
# SHUTTING_DOWN and best-effort signalled cupsd/avahi) -- this must not fall
# through to starting uvicorn against daemons that are already being told to
# stop. Finish stopping them (waiting for both to actually exit) and exit
# instead.
if [ "$SHUTTING_DOWN" = "1" ]; then
    kill -TERM $CUPS_PID $AVAHI_PID 2>/dev/null || true
    wait_for_pid "$CUPS_PID"
    wait_for_pid "$AVAHI_PID"
    exit 143
fi

# Start the application. Backgrounded (not exec'd, see the comment above) so
# this shell survives to catch SIGTERM/SIGINT, forward it, and then wait for
# every daemon to actually finish exiting before this shell does.
uvicorn app.main:app --host 0.0.0.0 --port 8080 &
UVICORN_PID=$!
wait_for_pid "$UVICORN_PID"
rc=$WAIT_RC
kill -TERM $CUPS_PID $AVAHI_PID 2>/dev/null || true
wait_for_pid "$CUPS_PID"
wait_for_pid "$AVAHI_PID"
exit "$rc"
