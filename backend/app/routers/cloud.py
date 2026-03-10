from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models import CloudProvider, User

router = APIRouter()


@router.get("/providers")
async def list_providers(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List connected cloud storage providers for the current user."""
    result = await db.execute(
        select(CloudProvider).where(CloudProvider.user_id == user.id)
    )
    providers = result.scalars().all()
    return {
        "providers": [
            {
                "id": p.id,
                "provider": p.provider,
                "connected_at": p.connected_at.isoformat(),
            }
            for p in providers
        ]
    }


@router.delete("/disconnect/{provider_id}", status_code=204)
async def disconnect_provider(
    provider_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Disconnect a cloud storage provider."""
    result = await db.execute(
        select(CloudProvider).where(
            CloudProvider.id == provider_id,
            CloudProvider.user_id == user.id,
        )
    )
    provider = result.scalar_one_or_none()
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found")
    await db.delete(provider)
    await db.commit()
