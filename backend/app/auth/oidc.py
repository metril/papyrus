from authlib.integrations.starlette_client import OAuth
from starlette.config import Config

from app.config import settings

oauth = OAuth()


def setup_oauth():
    """Register the OIDC provider with authlib."""
    if not settings.oidc_issuer:
        return

    oauth.register(
        name="papyrus",
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret,
        server_metadata_url=f"{settings.oidc_issuer.rstrip('/')}/.well-known/openid-configuration",
        client_kwargs={"scope": settings.oidc_scopes},
    )
