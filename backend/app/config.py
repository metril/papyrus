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

    # Printer (CUPS)
    printer_name: str = "Brother_DCP_L2540DW"
    printer_uri: str = "ipp://192.168.1.100/ipp"

    # Scanner (SANE)
    scanner_device: str = "airscan:w:Brother DCP-L2540DW"

    # Storage
    scan_dir: str = "/app/data/scans"
    upload_dir: str = "/app/data/uploads"
    max_upload_size_mb: int = 50
    scan_retention_days: int = 7

    # Security
    encryption_key: str = ""  # Fernet key for encrypting secrets at rest

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

    model_config = {"env_prefix": "PAPYRUS_"}


settings = Settings()
