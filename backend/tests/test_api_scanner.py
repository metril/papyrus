"""Scanner API permission-scoping guard.

Only covers the bulk-delete permission fix (F8) — general scanner behavior
(escl, profiles, enhancement, etc.) is covered elsewhere. Mirrors
test_api_jobs.py's bulk-delete token-scoping test.
"""
from sqlalchemy import select

from app.auth.tokens import hash_token
from app.models import APIToken, ScanJob, User


async def test_bulk_delete_scans_token_without_scan_permission_is_403(db, client):
    """Regression (F8): scanner bulk-delete used to depend on
    get_current_user, which performs no permission check, so a token scoped
    to only "print" could bulk-delete scans even though every sibling scan
    route requires require_permission("scan")."""
    scan = ScanJob(status="completed", filepath="/tmp/does-not-matter.pdf")
    db.add(scan)
    await db.commit()
    await db.refresh(scan)

    print_only_user = User(
        email="printonly-bulk@example.com", display_name="PrintOnlyBulk", role="user",
        is_local=True, username="printonly-bulk",
    )
    db.add(print_only_user)
    await db.commit()
    await db.refresh(print_only_user)
    plaintext = "pprs_test_print_only_bulk_delete_scan"
    db.add(APIToken(
        user_id=print_only_user.id, name="print-only",
        token_hash=hash_token(plaintext), permissions=["print"],
    ))
    await db.commit()

    bulk_resp = await client.post(
        "/api/scanner/scans/bulk-delete",
        json={"scan_ids": [scan.scan_id]},
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert bulk_resp.status_code == 403

    # The scan must survive the rejected bulk-delete.
    result = await db.execute(select(ScanJob).where(ScanJob.scan_id == scan.scan_id))
    assert result.scalar_one_or_none() is not None
