"""Regression test for DELETE /api/admin/users/{id} — F62.

CloudProvider.user_id and SMBShare.created_by used to be non-nullable FKs
with no `ondelete`, so `delete_user`'s bare `db.delete(target)` hit a
Postgres FK violation (generic 500) whenever the target user had connected a
cloud provider or created an SMB share. Migration 014 (Task 4) added
`ondelete="CASCADE"` to both FKs (see app/models.py). `delete_user` itself
does nothing special for either table — no ORM `relationship()` exists from
User to CloudProvider/SMBShare, so there's nothing for it to "fight"; the
cascade is enforced entirely at the DB level when the row is deleted. This
proves that DB-level cascade actually fires end-to-end through the API.
"""
from sqlalchemy import select

from app.models import CloudProvider, User


async def test_delete_user_with_cloud_provider_cascades(db, admin_client):
    target = User(
        email="drive-user@example.com",
        display_name="Drive User",
        role="user",
        is_local=True,
        username="driveuser",
    )
    db.add(target)
    await db.flush()

    db.add(
        CloudProvider(
            user_id=target.id,
            provider="gdrive",
            access_token_encrypted="ciphertext",
        )
    )
    await db.commit()
    target_id = target.id

    resp = await admin_client.delete(f"/api/admin/users/{target_id}")
    assert resp.status_code == 204

    # Fresh SELECTs, not db.get() -- the delete committed on the router's own
    # session, and this session's identity map would otherwise still hand
    # back the pre-delete in-memory object for a plain db.get() lookup.
    user_result = await db.execute(select(User).where(User.id == target_id))
    assert user_result.scalar_one_or_none() is None

    provider_result = await db.execute(
        select(CloudProvider).where(CloudProvider.user_id == target_id)
    )
    assert provider_result.scalar_one_or_none() is None
