import time
import uuid
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyCookie, HTTPBearer
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.tokens import validate_token
from app.database import get_db
from app.models import Kiosk, User

ALL_PERMISSIONS = ["print", "scan", "files", "admin", "email", "device"]

# Process-local kiosk last_seen tracking: token id -> (is_kiosk, monotonic time of last check).
# A non-kiosk token is looked up once and never touches the DB for this again; a kiosk token is
# written at most once per interval.
KIOSK_SEEN_INTERVAL = 30.0
_seen_checked: dict[uuid.UUID, tuple[bool, float]] = {}


async def _touch_kiosk(db: AsyncSession, token_id: uuid.UUID) -> None:
    now = time.monotonic()
    entry = _seen_checked.get(token_id)
    if entry is not None and (not entry[0] or now - entry[1] < KIOSK_SEEN_INTERVAL):
        return
    if len(_seen_checked) > 10_000:
        _seen_checked.clear()
    if entry is None:
        found = await db.execute(select(Kiosk.id).where(Kiosk.token_id == token_id))
        if found.first() is None:
            _seen_checked[token_id] = (False, now)
            return
    _seen_checked[token_id] = (True, now)
    await db.execute(
        update(Kiosk).where(Kiosk.token_id == token_id)
        .values(last_seen_at=datetime.now(timezone.utc))
    )
    await db.commit()


# Schema-only: these document the auth schemes in OpenAPI. Actual
# authentication is done by hand in get_current_user (auto_error=False and the
# credentials are never read from these dependencies).
_bearer_scheme = HTTPBearer(
    auto_error=False, description="API token (Authorization: Bearer <token>)"
)
_cookie_scheme = APIKeyCookie(
    name="session", auto_error=False, description="Session cookie from login"
)


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _bearer: object = Depends(_bearer_scheme),
    _cookie: object = Depends(_cookie_scheme),
) -> User:
    """Get the current authenticated user from session cookie or API token.

    Checks in order:
    1. Authorization: Bearer <token> header (API token)
    2. Session cookie (OIDC login)
    """
    # Check for Bearer token
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        plaintext = auth_header[7:]
        token = await validate_token(db, plaintext)
        if token is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired API token",
            )
        # Load the token's user
        result = await db.execute(select(User).where(User.id == token.user_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token user not found",
            )
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="This account has been deactivated",
            )
        # Attach token permissions to request state for downstream checks
        request.state.token_permissions = token.permissions
        request.state.token_id = token.id
        await _touch_kiosk(db, token.id)
        return user

    # Check for session cookie
    user_id = request.session.get("user_id")
    if user_id:
        try:
            session_uuid = uuid.UUID(user_id)
        except (ValueError, TypeError, AttributeError):
            session_uuid = None
        if session_uuid is not None:
            result = await db.execute(select(User).where(User.id == session_uuid))
            user = result.scalar_one_or_none()
            if user and user.is_active:
                request.state.token_permissions = None  # Full access via session
                request.state.token_id = None
                return user

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
    )


async def require_admin(
    request: Request,
    user: User = Depends(get_current_user),
) -> User:
    """Require that the current user is an admin (role + token permission)."""
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    token_permissions = getattr(request.state, "token_permissions", None)
    if token_permissions is not None and "admin" not in token_permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token missing required permission: admin",
        )
    return user


def require_permission(permission: str):
    """Create a dependency that checks for a specific permission.

    For session users, all permissions are granted.
    For API token users, the permission must be in the token's permission list.
    """
    async def checker(
        request: Request,
        user: User = Depends(get_current_user),
    ) -> User:
        token_permissions = getattr(request.state, "token_permissions", None)
        if token_permissions is not None and permission not in token_permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Token missing required permission: {permission}",
            )
        return user

    return checker
