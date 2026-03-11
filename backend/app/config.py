from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    db_url: str = "postgresql+asyncpg://papyrus:secret@localhost:5432/papyrus"

    # OIDC Authentication
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""

    # Session
    session_secret: str = "change-me-in-production"

    # Printer fallback name (used if no default printer configured in DB)
    printer_name: str = "Brother_DCP_L2540DW"

    # Scanner fallback device string (used if no default scanner configured in DB)
    scanner_device: str = "airscan:w:Brother DCP-L2540DW"

    # Storage
    scan_dir: str = "/app/data/scans"
    upload_dir: str = "/app/data/uploads"
    max_upload_size_mb: int = 50
    scan_retention_days: int = 7

    # Security
    encryption_key: str = ""  # Fernet key for encrypting secrets at rest

    # Development
    dev_mode: bool = False

    # Application
    base_url: str = "http://localhost:8080"
    host: str = "0.0.0.0"
    port: int = 8080

    # SMTP (optional, can also be configured via web UI)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""

    # Cloud Storage OAuth
    gdrive_client_id: str = ""
    gdrive_client_secret: str = ""
    dropbox_app_key: str = ""
    dropbox_app_secret: str = ""

    # Email Webhook
    email_webhook_secret: str = ""
    email_webhook_rate_limit: int = 10  # max requests per minute per IP

    # Network Services
    escl_enabled: bool = True

    model_config = {"env_prefix": "PAPYRUS_"}


settings = Settings()
