import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import APIToken


def generate_token() -> tuple[str, str]:
    """Generate a new API token and its hash.

    Returns:
        Tuple of (plaintext_token, token_hash). The plaintext is shown once to the user.
    """
    plaintext = f"pprs_{secrets.token_urlsafe(32)}"
    token_hash = hashlib.sha256(plaintext.encode()).hexdigest()
    return plaintext, token_hash


def hash_token(plaintext: str) -> str:
    """Hash a plaintext token for lookup."""
    return hashlib.sha256(plaintext.encode()).hexdigest()


async def validate_token(db: AsyncSession, plaintext: str) -> APIToken | None:
    """Validate an API token and return it if valid."""
    token_hash = hash_token(plaintext)
    result = await db.execute(
        select(APIToken).where(APIToken.token_hash == token_hash)
    )
    token = result.scalar_one_or_none()

    if token is None:
        return None

    # Check expiry
    if token.expires_at and token.expires_at < datetime.now(timezone.utc):
        return None

    # Update last_used_at
    token.last_used_at = datetime.now(timezone.utc)
    await db.commit()

    return token
