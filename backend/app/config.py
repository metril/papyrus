import logging

from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


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

    # Stop Avahi mDNS advertising (AirPrint, eSCL, per-printer adverts).
    disable_mdns: bool = False

    # Shared secret the papyrus CUPS backend script sends on every
    # /api/jobs/internal/ingest POST (F7). Generated per-container by
    # docker/entrypoint.sh; empty (default) only in local/dev setups that
    # never run the real CUPS backend against this endpoint.
    ingest_token: str = ""

    # CORS: comma-separated list of allowed origins. Empty (default) means
    # same-origin only — CORSMiddleware is not added at all, since the app
    # is served from behind a reverse proxy on the same origin.
    cors_origins: str = ""

    model_config = {"env_prefix": "PAPYRUS_"}

    @property
    def cors_origins_list(self) -> list[str]:
        """Parsed, whitespace-trimmed list of allowed CORS origins."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()


def validate_runtime_secrets() -> None:
    """Hard-fail startup if `PAPYRUS_SESSION_SECRET` is unset or still the
    placeholder default.

    Starlette's `SessionMiddleware` signs auth cookies with this key
    (`app.main` adds it with `secret_key=settings.session_secret`) and a
    session cookie is full authentication (`app/auth/dependencies.py`
    trusts `session["user_id"]` outright) — an empty or well-known key lets
    an attacker forge any user's session, including an admin's. Only
    `PAPYRUS_DEV_MODE` downgrades this to a warning, since dev mode already
    bypasses OIDC.
    """
    if settings.session_secret in ("", "change-me-in-production"):
        if settings.dev_mode:
            logger.warning(
                "PAPYRUS_SESSION_SECRET is not set (or is the default "
                "placeholder) — session cookies are forgeable. Continuing "
                "only because PAPYRUS_DEV_MODE is enabled."
            )
        else:
            raise RuntimeError(
                "PAPYRUS_SESSION_SECRET must be set to a random, unique "
                "value in production — refusing to start with an empty or "
                "placeholder session secret."
            )
