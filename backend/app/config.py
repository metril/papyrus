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

    # OIDC (all env-var only — needed at startup before login)
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_scopes: str = "openid email profile"
    oidc_admin_group: str = ""        # OIDC group name that grants admin role
    oidc_groups_claim: str = "groups"  # claim name containing group list

    # Server
    base_url: str = "http://localhost:8080"
    host: str = "0.0.0.0"
    port: int = 8080

    # Development
    dev_mode: bool = False

    model_config = {"env_prefix": "PAPYRUS_"}


settings = Settings()
