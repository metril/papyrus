# Stage 1: Build frontend
FROM node:20-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Runtime
FROM python:3.12-slim-bookworm

# Install system dependencies: CUPS, SANE, brlaser, sane-airscan, LibreOffice, smbclient
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cups \
    cups-client \
    printer-driver-brlaser \
    libcups2-dev \
    sane-utils \
    sane-airscan \
    libsane-dev \
    img2pdf \
    ghostscript \
    libreoffice-core \
    libreoffice-writer \
    libreoffice-calc \
    libreoffice-impress \
    smbclient \
    libsmbclient-dev \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
WORKDIR /app
COPY backend/pyproject.toml ./
RUN pip install --no-cache-dir ".[cloud]"

# Copy backend code
COPY backend/ ./backend/

# Copy built frontend
COPY --from=frontend-build /app/frontend/dist ./static/

# Copy CUPS and SANE configuration
COPY docker/cups/cupsd.conf /etc/cups/cupsd.conf
COPY docker/sane/ /etc/sane.d/

# Create data directories
RUN mkdir -p /app/data/scans /app/data/uploads

# Volumes for persistent data
VOLUME ["/app/data", "/var/spool/cups"]

# Expose app port
EXPOSE 8080

# Entrypoint
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
