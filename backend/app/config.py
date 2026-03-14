from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── Infrastructure (env-var only — needed before app starts) ──
    db_url: str = "postgresql+asyncpg://papyrus:secret@localhost:5432/papyrus"
    session_secret: str = "change-me-in-production"
    encryption_key: str = ""  # Fernet key for encrypting secrets at rest
    host: str = "0.0.0.0"
    port: int = 8080

    # ── OIDC (env-var only — OAuth registered at startup) ──
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""

    # ── Configurable via Settings UI (env vars are fallback defaults) ──

    # OIDC group mapping (runtime-configurable via UI)
    oidc_scopes: str = "openid email profile"
    oidc_admin_group: str = ""
    oidc_groups_claim: str = "groups"

    # Application
    base_url: str = "http://localhost:8080"
    dev_mode: bool = False
    require_release_pin: bool = False

    # Storage
    scan_dir: str = "/app/data/scans"
    upload_dir: str = "/app/data/uploads"
    max_upload_size_mb: int = 50

    # Retention
    scan_retention_days: int = 7
    print_retention_days: int = 30
    scan_filename_template: str = "scan_{date}_{time}_{id}"

    # SMTP
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""

    # Email Webhook
    email_webhook_secret: str = ""
    email_webhook_rate_limit: int = 10

    # Cloud Storage OAuth
    gdrive_client_id: str = ""
    gdrive_client_secret: str = ""
    dropbox_app_key: str = ""
    dropbox_app_secret: str = ""
    onedrive_client_id: str = ""
    onedrive_client_secret: str = ""

    # Paperless-ngx
    paperless_url: str = ""
    paperless_api_token: str = ""

    # OCR
    ocr_enabled: bool = False
    ocr_language: str = "eng"

    # FTP/SFTP
    ftp_host: str = ""
    ftp_port: int = 21
    ftp_username: str = ""
    ftp_password: str = ""
    ftp_remote_dir: str = "/"
    ftp_protocol: str = "ftp"

    # Network Services
    escl_enabled: bool = True

    # ── Hardware fallbacks (empty — managed via Printers/Scanners DB tables) ──
    printer_name: str = ""
    scanner_device: str = ""

    model_config = {"env_prefix": "PAPYRUS_"}


settings = Settings()
