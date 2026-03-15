from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_admin
from app.auth.oidc import ensure_oidc_registered, oauth
from app.auth.tokens import generate_token
from app.config import settings
from app.database import get_db
from app.models import APIToken, User
from app.routers.settings import get_setting
from app.schemas import (
    APITokenCreate,
    APITokenCreated,
    APITokenResponse,
    UserResponse,
)

router = APIRouter()


# --- Auth Providers ---


@router.get("/providers")
async def get_providers(db: AsyncSession = Depends(get_db)) -> dict:
    """Return available auth methods (public, no auth required)."""
    local_enabled = (await get_setting(db, "local_auth_enabled") or "true").lower() in ("true", "1", "yes")
    oidc_enabled = (await get_setting(db, "oidc_enabled") or "false").lower() in ("true", "1", "yes")
    oidc_issuer = await get_setting(db, "oidc_issuer") or ""
    return {
        "local_enabled": local_enabled,
        "oidc_enabled": oidc_enabled and bool(oidc_issuer),
    }


# --- Local Login ---


class LocalLoginRequest(BaseModel):
    username: str
    password: str


@router.post("/local-login")
async def local_login(
    body: LocalLoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Authenticate with username and password."""
    local_enabled = (await get_setting(db, "local_auth_enabled") or "true").lower() in ("true", "1", "yes")
    if not local_enabled:
        raise HTTPException(status_code=403, detail="Local login is disabled")

    result = await db.execute(
        select(User).where(User.username == body.username, User.is_local == True)
    )
    user = result.scalar_one_or_none()
    if not user or not user.password_hash:
        raise HTTPException(status_code=401, detail="Invalid username or password")

    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError
    ph = PasswordHasher()
    try:
        ph.verify(user.password_hash, body.password)
    except VerifyMismatchError:
        raise HTTPException(status_code=401, detail="Invalid username or password")

    # Rehash if needed (argon2-cffi best practice)
    if ph.check_needs_rehash(user.password_hash):
        user.password_hash = ph.hash(body.password)

    user.last_login = datetime.now(timezone.utc)
    await db.commit()

    request.session["user_id"] = str(user.id)
    return {"message": "logged in", "user_id": str(user.id)}


# --- OIDC Login ---


@router.get("/login")
async def oidc_login(request: Request, db: AsyncSession = Depends(get_db)):
    """Redirect to OIDC provider for authentication."""
    # Dev mode auto-login
    if settings.dev_mode:
        result = await db.execute(select(User).where(User.username == "dev-admin"))
        user = result.scalar_one_or_none()
        if user is None:
            user = User(
                username="dev-admin",
                email="dev@papyrus.local",
                display_name="Dev Admin",
                role="admin",
                is_local=True,
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)
        request.session["user_id"] = str(user.id)
        return RedirectResponse(url="/", status_code=302)

    # Check OIDC config from DB
    oidc_enabled = (await get_setting(db, "oidc_enabled") or "false").lower() in ("true", "1", "yes")
    if not oidc_enabled:
        raise HTTPException(status_code=503, detail="OIDC not enabled")

    issuer = await get_setting(db, "oidc_issuer") or ""
    client_id = await get_setting(db, "oidc_client_id") or ""
    client_secret = await get_setting(db, "oidc_client_secret") or ""
    scopes = await get_setting(db, "oidc_scopes") or "openid email profile"

    if not ensure_oidc_registered(issuer, client_id, client_secret, scopes):
        raise HTTPException(status_code=503, detail="OIDC not configured")

    redirect_uri = f"{settings.base_url}/api/auth/callback"
    return await oauth.papyrus.authorize_redirect(request, redirect_uri)


@router.get("/callback")
async def oidc_callback(request: Request, db: AsyncSession = Depends(get_db)):
    """Handle OIDC callback and create/update user."""
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

    # Determine role from OIDC groups claim (if configured)
    admin_group = await get_setting(db, "oidc_admin_group") or ""
    groups_claim = await get_setting(db, "oidc_groups_claim") or "groups"
    role: str | None = None
    if admin_group:
        groups = userinfo.get(groups_claim, [])
        if isinstance(groups, str):
            groups = [groups]
        role = "admin" if admin_group in groups else "user"

    if user is None:
        user = User(
            oidc_sub=oidc_sub,
            email=email,
            display_name=display_name,
            role=role or "user",
            is_local=False,
        )
        db.add(user)
    else:
        user.email = email
        user.display_name = display_name
        if role is not None:
            user.role = role

    user.last_login = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(user)

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
