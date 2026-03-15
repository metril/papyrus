"""Dynamic OIDC provider registration.

Instead of registering at startup, the OIDC provider is registered
lazily on first login request using settings from the database.
"""
from authlib.integrations.starlette_client import OAuth

oauth = OAuth()
_registered = False


def ensure_oidc_registered(issuer: str, client_id: str, client_secret: str, scopes: str) -> bool:
    """Register the OIDC provider if not already registered.

    Returns True if OIDC is ready, False if missing config.
    """
    global _registered

    if not issuer or not client_id or not client_secret:
        return False

    if _registered:
        # Already registered — authlib doesn't support re-registration
        # Changing OIDC config requires app restart
        return True

    oauth.register(
        name="papyrus",
        client_id=client_id,
        client_secret=client_secret,
        server_metadata_url=f"{issuer.rstrip('/')}/.well-known/openid-configuration",
        client_kwargs={"scope": scopes or "openid email profile"},
    )
    _registered = True
    return True
