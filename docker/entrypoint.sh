#!/bin/bash
set -e

# Start CUPS daemon
cupsd

# Wait for CUPS to be ready
sleep 2

# Configure printer if not already added
if ! lpstat -p "${PAPYRUS_PRINTER_NAME:-Brother_DCP_L2540DW}" 2>/dev/null; then
    echo "Configuring printer ${PAPYRUS_PRINTER_NAME:-Brother_DCP_L2540DW}..."
    lpadmin -p "${PAPYRUS_PRINTER_NAME:-Brother_DCP_L2540DW}" \
        -v "${PAPYRUS_PRINTER_URI:-ipp://192.168.1.100/ipp}" \
        -m drv:///brlaser.drv/brl2540dw.ppd \
        -E
    cupsenable "${PAPYRUS_PRINTER_NAME:-Brother_DCP_L2540DW}"
    cupsaccept "${PAPYRUS_PRINTER_NAME:-Brother_DCP_L2540DW}"
    echo "Printer configured successfully."
fi

# Create data directories
mkdir -p "${PAPYRUS_SCAN_DIR:-/app/data/scans}" "${PAPYRUS_UPLOAD_DIR:-/app/data/uploads}"

# Run database migrations
cd /app/backend
python -m alembic upgrade head

# Start the application
exec uvicorn app.main:app --host 0.0.0.0 --port 8080
