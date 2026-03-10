from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import DEV_OIDC_SUB, get_current_user, require_admin
from app.auth.oidc import oauth
from app.auth.tokens import generate_token
from app.config import settings
from app.database import get_db
from app.models import APIToken, User
from app.schemas import (
    APITokenCreate,
    APITokenCreated,
    APITokenResponse,
    UserResponse,
)

router = APIRouter()


@router.get("/login")
async def login(request: Request, db: AsyncSession = Depends(get_db)):
    """Redirect to OIDC provider for authentication."""
    if not settings.oidc_issuer:
        if settings.dev_mode:
            # Auto-login as dev admin
            result = await db.execute(select(User).where(User.oidc_sub == DEV_OIDC_SUB))
            user = result.scalar_one_or_none()
            if user is None:
                user = User(
                    oidc_sub=DEV_OIDC_SUB,
                    email="dev@papyrus.local",
                    display_name="Dev Admin",
                    role="admin",
                )
                db.add(user)
                await db.commit()
                await db.refresh(user)
            request.session["user_id"] = str(user.id)
            return RedirectResponse(url="/", status_code=302)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OIDC not configured",
        )
    redirect_uri = f"{settings.base_url}/api/auth/callback"
    return await oauth.papyrus.authorize_redirect(request, redirect_uri)


@router.get("/callback")
async def callback(request: Request, db: AsyncSession = Depends(get_db)):
    """Handle OIDC callback and create/update local user."""
    token = await oauth.papyrus.authorize_access_token(request)
    userinfo = token.get("userinfo")
    if not userinfo:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Failed to get user info from OIDC provider",
        )

    oidc_sub = userinfo["sub"]
    email = userinfo.get("email", "")
    display_name = userinfo.get("name", email)

    # Find or create user
    result = await db.execute(select(User).where(User.oidc_sub == oidc_sub))
    user = result.scalar_one_or_none()

    if user is None:
        # Check if this is the first user (make them admin)
        count_result = await db.execute(select(User))
        is_first_user = count_result.first() is None

        user = User(
            oidc_sub=oidc_sub,
            email=email,
            display_name=display_name,
            role="admin" if is_first_user else "user",
        )
        db.add(user)
    else:
        user.email = email
        user.display_name = display_name

    user.last_login = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(user)

    # Set session
    request.session["user_id"] = str(user.id)

    return RedirectResponse(url="/", status_code=302)


@router.post("/logout")
async def logout(request: Request):
    """Clear the session."""
    request.session.clear()
    return {"message": "logged out"}


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)):
    """Get the current authenticated user."""
    return user


# --- API Token Management ---


@router.get("/tokens", response_model=list[APITokenResponse])
async def list_tokens(
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List all API tokens (admin only)."""
    result = await db.execute(select(APIToken).order_by(APIToken.created_at.desc()))
    return result.scalars().all()


@router.post("/tokens", response_model=APITokenCreated, status_code=201)
async def create_token(
    data: APITokenCreate,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Create a new API token (admin only). The plaintext token is returned only once."""
    plaintext, token_hash = generate_token()

    expires_at = None
    if data.expires_in_days:
        expires_at = datetime.now(timezone.utc) + timedelta(days=data.expires_in_days)

    token = APIToken(
        user_id=user.id,
        name=data.name,
        token_hash=token_hash,
        permissions=data.permissions,
        expires_at=expires_at,
    )
    db.add(token)
    await db.commit()
    await db.refresh(token)

    return APITokenCreated(
        id=token.id,
        name=token.name,
        permissions=token.permissions,
        expires_at=token.expires_at,
        created_at=token.created_at,
        last_used_at=token.last_used_at,
        token=plaintext,
    )


@router.delete("/tokens/{token_id}", status_code=204)
async def revoke_token(
    token_id: str,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Revoke an API token (admin only)."""
    result = await db.execute(select(APIToken).where(APIToken.id == token_id))
    token = result.scalar_one_or_none()
    if token is None:
        raise HTTPException(status_code=404, detail="Token not found")
    await db.delete(token)
    await db.commit()
