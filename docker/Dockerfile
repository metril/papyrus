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
    avahi-daemon \
    avahi-utils \
    libnss-mdns \
    tesseract-ocr \
    ocrmypdf \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Brother brscan4 network scanner driver
RUN curl -fsSL "https://download.brother.com/welcome/dlf105200/brscan4-0.4.11-1.amd64.deb" \
      -o /tmp/brscan4.deb \
    && dpkg -i /tmp/brscan4.deb \
    && rm /tmp/brscan4.deb

# Python dependencies (create minimal package for dependency caching layer)
WORKDIR /app/backend
COPY backend/pyproject.toml ./
RUN mkdir -p app && touch app/__init__.py && pip install --no-cache-dir ".[cloud,ocr,sftp]"

# Copy backend code (overwrites minimal stub)
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
RUN mkdir -p /etc/cups/ppd
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
