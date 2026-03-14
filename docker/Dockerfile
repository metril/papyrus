# Stage 1: Build frontend
FROM node:20-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Build Python wheels (includes build-time deps only)
FROM python:3.12-slim-bookworm AS python-build
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libcups2-dev \
    libsane-dev \
    libsmbclient-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app/backend
COPY backend/pyproject.toml ./
RUN mkdir -p app && touch app/__init__.py \
    && pip wheel --no-cache-dir -w /wheels ".[cloud,ocr,sftp]"

# Stage 3: Runtime (no build tools, no -dev packages)
FROM python:3.12-slim-bookworm

# Install runtime-only system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    cups \
    cups-client \
    libcups2 \
    sane-utils \
    sane-airscan \
    libsane1 \
    img2pdf \
    ghostscript \
    libreoffice-core \
    libreoffice-writer \
    libreoffice-calc \
    libreoffice-impress \
    smbclient \
    libsmbclient \
    avahi-daemon \
    avahi-utils \
    libnss-mdns \
    tesseract-ocr \
    ocrmypdf \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install pre-built Python wheels (no compilation needed)
COPY --from=python-build /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels

# Copy backend code
WORKDIR /app/backend
COPY backend/ ./

# Copy built frontend
COPY --from=frontend-build /app/frontend/dist ./static/

# Copy CUPS, SANE, and Avahi configuration
COPY docker/cups/cupsd.conf /etc/cups/cupsd.conf
COPY docker/sane/ /etc/sane.d/
COPY docker/avahi/avahi-daemon.conf /etc/avahi/avahi-daemon.conf
COPY docker/avahi/escl.service /etc/avahi/services/escl.service
COPY docker/avahi/airprint.service /etc/avahi/services/airprint.service
COPY docker/cups/papyrus-backend /usr/lib/cups/backend/papyrus
RUN chmod 700 /usr/lib/cups/backend/papyrus
RUN mkdir -p /etc/cups/ppd /etc/sane.d/airscan.d
COPY docker/cups/papyrus.ppd /etc/cups/ppd/papyrus.ppd

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
