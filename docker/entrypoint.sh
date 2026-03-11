#!/bin/bash
set -e

# Start Avahi mDNS daemon for network discovery (AirPrint + eSCL scanner)
mkdir -p /run/avahi-daemon
avahi-daemon --daemonize --no-chroot --no-drop-root || echo "Warning: avahi-daemon failed to start"

# Start CUPS daemon
cupsd

# Wait for CUPS to be ready
sleep 2

# Configure physical printer if not already added
if ! lpstat -p "${PAPYRUS_PRINTER_NAME:-Brother_DCP_L2540DW}" 2>/dev/null; then
    echo "Configuring printer ${PAPYRUS_PRINTER_NAME:-Brother_DCP_L2540DW}..."
    lpadmin -p "${PAPYRUS_PRINTER_NAME:-Brother_DCP_L2540DW}" \
        -v "${PAPYRUS_PRINTER_URI:-ipp://192.168.1.100/ipp}" \
        -m everywhere \
        -E
    cupsenable "${PAPYRUS_PRINTER_NAME:-Brother_DCP_L2540DW}"
    cupsaccept "${PAPYRUS_PRINTER_NAME:-Brother_DCP_L2540DW}"
    echo "Printer configured successfully."
fi

# Configure network-facing Papyrus printer queue (uses custom backend)
NETWORK_PRINTER="${PAPYRUS_NETWORK_PRINTER_NAME:-Papyrus}"
if ! lpstat -p "$NETWORK_PRINTER" 2>/dev/null; then
    echo "Configuring network printer queue '$NETWORK_PRINTER'..."
    lpadmin -p "$NETWORK_PRINTER" \
        -v papyrus:/ \
        -P /etc/cups/ppd/papyrus.ppd \
        -o printer-is-shared=true \
        -E
    cupsenable "$NETWORK_PRINTER"
    cupsaccept "$NETWORK_PRINTER"
    echo "Network printer queue configured."
fi

# Create data directories
mkdir -p "${PAPYRUS_SCAN_DIR:-/app/data/scans}" "${PAPYRUS_UPLOAD_DIR:-/app/data/uploads}"

# Run database migrations
cd /app/backend
python -m alembic upgrade head

# Start the application
exec uvicorn app.main:app --host 0.0.0.0 --port 8080
