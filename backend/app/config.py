from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Infrastructure settings — env-var only, needed before app starts.

    All other settings are managed via the Settings UI and stored in the
    AppConfig database table. Use get_setting() to read them.
    """

    # Database
    db_url: str = "postgresql+asyncpg://papyrus:secret@localhost:5432/papyrus"

    # Session
    session_secret: str = "change-me-in-production"

    # Encryption
    encryption_key: str = ""  # Fernet key for encrypting secrets at rest

    # Local admin account (created on first startup if no admin exists)
    admin_username: str = ""
    admin_password: str = ""

    # Server
    base_url: str = "http://localhost:8080"
    host: str = "0.0.0.0"
    port: int = 8080

    # Development
    dev_mode: bool = False

    model_config = {"env_prefix": "PAPYRUS_"}


settings = Settings()
