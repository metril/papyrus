import uuid
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import User
from app.auth.tokens import validate_token

ALL_PERMISSIONS = ["print", "scan", "files", "admin", "email"]


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
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
        # Attach token permissions to request state for downstream checks
        request.state.token_permissions = token.permissions
        return user

    # Check for session cookie
    user_id = request.session.get("user_id")
    if user_id:
        result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
        user = result.scalar_one_or_none()
        if user:
            request.state.token_permissions = None  # Full access via session
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
