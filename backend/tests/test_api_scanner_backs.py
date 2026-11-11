"""POST /api/scanner/scans/{id}/backs through the real route (DB harness).

scan_service is faked at scanner.py's import site so no scanimage runs; the
fake writes a real back-side PDF.
"""
import os

import pytest
from sqlalchemy import select

from app.auth.tokens import hash_token
from app.database import async_session
from app.exceptions import ScannerBusyError
from app.models import APIToken, ScanJob, Scanner, User
from app.routers import scanner as scanner_router
from app.services import duplex_service
from app.services.scan_service import ScanError
from tests.duplex_helpers import make_pdf, tags


class _FakeScanService:
    def __init__(self, tmp_path, specs, error=None):
        self.tmp_path, self.specs, self.error = tmp_path, specs, error
        self.calls: list[dict] = []
        self.during_scan = None  # async hook: something happening while paper moves

    async def scan_batch(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        n = len(self.calls)
        suffix = "" if n == 1 else f"-{n}"
        path = str(self.tmp_path / f"backs{suffix}.pdf")
        make_pdf(path, self.specs)
        if self.during_scan:
            await self.during_scan()
        return f"backs-scan-id{suffix}", path, len(self.specs)


async def _fake_device(_db) -> str:
    return "test:device0"


def _patch(monkeypatch, tmp_path, specs, error=None):
    from app.routers import settings as settings_router

    real_get_setting = settings_router.get_setting

    async def get_setting(db, key):
        return str(tmp_path) if key == "scan_dir" else await real_get_setting(db, key)

    monkeypatch.setattr(settings_router, "get_setting", get_setting)
    fake = _FakeScanService(tmp_path, specs, error)
    monkeypatch.setattr(scanner_router, "scan_service", fake)
    monkeypatch.setattr(scanner_router, "get_default_scanner_device", _fake_device)
    return fake


async def _seed(db, tmp_path, fronts, **overrides) -> ScanJob:
    path = str(tmp_path / "fronts.pdf")
    make_pdf(path, fronts)
    fields = dict(
        scan_id="front-scan-id", status="completed", source="ADF", format="pdf",
        resolution=200, mode="Gray", page_count=len(fronts), filepath=path,
        file_size=os.path.getsize(path),
    )
    fields.update(overrides)
    job = ScanJob(**fields)
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


async def _row(db, scan_id) -> ScanJob:
    result = await db.execute(select(ScanJob).where(ScanJob.scan_id == scan_id))
    row = result.scalar_one()
    await db.refresh(row)
    return row


async def _all(db) -> list[ScanJob]:
    return list((await db.execute(select(ScanJob))).scalars().all())


URL = "/api/scanner/scans/front-scan-id/backs"
FRONTS = [(1, "text"), (2, "text"), (3, "text")]
BACKS = [(11, "text"), (12, "text"), (13, "text")]


async def test_merge_creates_new_row_and_keeps_fronts_and_backs(
    db, user_client, monkeypatch, tmp_path
):
    job = await _seed(db, tmp_path, FRONTS)
    before = open(job.filepath, "rb").read()
    fake = _patch(monkeypatch, tmp_path, BACKS)
    resp = await user_client.post(URL, json={})
    assert resp.status_code == 200
    body = resp.json()
    merged = body["merged"]
    assert merged["scan_id"] not in ("front-scan-id", "backs-scan-id")
    assert merged["page_count"] == 6 and merged["duplex_merged"] is True
    assert merged["source"] == "ADF" and merged["status"] == "completed"
    assert body["blanks_dropped"] == 0
    merged_row = await _row(db, merged["scan_id"])
    assert tags(merged_row.filepath) == [1, 13, 2, 12, 3, 11]
    assert merged["file_size"] == os.path.getsize(merged_row.filepath)
    # Fronts untouched on disk; backs kept as their own completed row.
    assert open(job.filepath, "rb").read() == before
    backs = await _row(db, "backs-scan-id")
    assert backs.status == "completed" and backs.page_count == 3 and not backs.duplex_merged
    assert os.path.exists(backs.filepath)
    fronts = await _row(db, "front-scan-id")
    assert fronts.duplex_merged is True and fronts.page_count == 3
    assert len(await _all(db)) == 3
    assert fake.calls[0]["resolution"] == 200 and fake.calls[0]["mode"] == "Gray"
    assert fake.calls[0]["device"] == "test:device0"


async def test_broadcasts_scan_completed_per_row(db, user_client, monkeypatch, tmp_path):
    await _seed(db, tmp_path, FRONTS)
    _patch(monkeypatch, tmp_path, BACKS)
    sent = []

    async def fake_broadcast(channel, message, owner_id=None):
        sent.append((channel, message))

    monkeypatch.setattr(scanner_router.ws_manager, "broadcast", fake_broadcast)
    resp = await user_client.post(URL, json={})
    ids = [
        m["data"]["scan_id"] for c, m in sent
        if c == "scans" and m["type"] == "scan_completed"
    ]
    merged_id = resp.json()["merged"]["scan_id"]
    # fronts, backs and merged right after the commit, merged again after delivery
    assert ids == ["front-scan-id", "backs-scan-id", merged_id, merged_id]


async def test_blank_removal_is_off_by_default_and_opt_in(db, user_client, monkeypatch, tmp_path):
    await _seed(db, tmp_path, FRONTS)
    _patch(monkeypatch, tmp_path, [(11, "blank"), (12, "text"), (13, "blank")])
    resp = await user_client.post(URL, json={})
    assert resp.json()["merged"]["page_count"] == 6 and resp.json()["blanks_dropped"] == 0


async def test_blank_backs_removed_when_requested(db, user_client, monkeypatch, tmp_path):
    await _seed(db, tmp_path, FRONTS)
    _patch(monkeypatch, tmp_path, [(11, "blank"), (12, "text"), (13, "blank")])
    resp = await user_client.post(URL, json={"skip_blank_backs": True, "rotate_backs": True})
    body = resp.json()
    assert body["merged"]["page_count"] == 4 and body["blanks_dropped"] == 2
    assert tags((await _row(db, body["merged"]["scan_id"])).filepath) == [1, 2, 12, 3]


async def test_reverse_backs_false(db, user_client, monkeypatch, tmp_path):
    await _seed(db, tmp_path, [(1, "text"), (2, "text")])
    _patch(monkeypatch, tmp_path, [(11, "text"), (12, "text")])
    resp = await user_client.post(URL, json={"reverse_backs": False})
    assert tags((await _row(db, resp.json()["merged"]["scan_id"])).filepath) == [1, 11, 2, 12]


async def test_count_mismatch_keeps_both_and_creates_no_merge(
    db, user_client, monkeypatch, tmp_path
):
    job = await _seed(db, tmp_path, FRONTS)
    before = open(job.filepath, "rb").read()
    _patch(monkeypatch, tmp_path, [(11, "text"), (12, "text")])
    resp = await user_client.post(URL, json={})
    assert resp.status_code == 409
    assert "history" in resp.json()["detail"]
    assert open(job.filepath, "rb").read() == before
    kept = await _row(db, "backs-scan-id")
    assert kept.status == "completed" and kept.page_count == 2
    assert tags(kept.filepath) == [11, 12]
    assert (await _row(db, "front-scan-id")).duplex_merged is False
    assert len(await _all(db)) == 2


async def test_merge_failure_keeps_original_and_backs(db, user_client, monkeypatch, tmp_path):
    job = await _seed(db, tmp_path, FRONTS)
    before = open(job.filepath, "rb").read()
    _patch(monkeypatch, tmp_path, BACKS)

    def boom(*a, **k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(duplex_service, "merge_backs_atomic", boom)
    resp = await user_client.post(URL, json={})
    assert resp.status_code == 409
    assert "disk" not in resp.json()["detail"]
    assert open(job.filepath, "rb").read() == before
    assert os.path.exists((await _row(db, "backs-scan-id")).filepath)
    assert (await _row(db, "front-scan-id")).duplex_merged is False
    assert len(await _all(db)) == 2


@pytest.mark.parametrize("overrides", [
    {"source": "Flatbed"}, {"format": "png"}, {"status": "failed"}, {"filepath": None},
    {"duplex_merged": True},
])
async def test_ineligible_rows_refused(db, user_client, monkeypatch, tmp_path, overrides):
    await _seed(db, tmp_path, FRONTS, **overrides)
    fake = _patch(monkeypatch, tmp_path, BACKS)
    resp = await user_client.post(URL, json={})
    assert resp.status_code == 400
    assert resp.json()["detail"]
    assert fake.calls == []


async def test_fronts_that_already_merged_cannot_merge_again(
    db, user_client, monkeypatch, tmp_path
):
    await _seed(db, tmp_path, FRONTS)
    _patch(monkeypatch, tmp_path, BACKS)
    assert (await user_client.post(URL, json={})).status_code == 200
    fake = _patch(monkeypatch, tmp_path, BACKS)
    again = await user_client.post(URL, json={})
    assert again.status_code == 400 and fake.calls == []


async def test_merged_result_cannot_be_merged(db, user_client, monkeypatch, tmp_path):
    await _seed(db, tmp_path, FRONTS)
    _patch(monkeypatch, tmp_path, BACKS)
    merged_id = (await user_client.post(URL, json={})).json()["merged"]["scan_id"]
    fake = _patch(monkeypatch, tmp_path, BACKS)
    resp = await user_client.post(f"/api/scanner/scans/{merged_id}/backs", json={})
    assert resp.status_code == 400 and fake.calls == []


async def test_missing_file_refused(db, user_client, monkeypatch, tmp_path):
    job = await _seed(db, tmp_path, FRONTS)
    os.unlink(job.filepath)
    fake = _patch(monkeypatch, tmp_path, BACKS)
    assert (await user_client.post(URL, json={})).status_code == 400
    assert fake.calls == []


async def test_unknown_scan_is_404(db, user_client, monkeypatch, tmp_path):
    fake = _patch(monkeypatch, tmp_path, [])
    assert (await user_client.post("/api/scanner/scans/nope/backs", json={})).status_code == 404
    assert fake.calls == []


@pytest.mark.parametrize("error", [
    ScanError("No pages scanned from ADF"), ScanError("Scanner is busy. Try again later."),
    ScannerBusyError("No default scanner configured"),
])
async def test_feeder_refusal_changes_nothing(db, user_client, monkeypatch, tmp_path, error):
    job = await _seed(db, tmp_path, FRONTS)
    before = open(job.filepath, "rb").read()
    _patch(monkeypatch, tmp_path, [], error=error)
    resp = await user_client.post(URL, json={})
    assert resp.status_code == error.status_code
    assert resp.json()["detail"] == error.detail
    assert open(job.filepath, "rb").read() == before
    assert len(await _all(db)) == 1
    assert (await _row(db, "front-scan-id")).duplex_merged is False


async def _other_user(db) -> User:
    other = User(email="o@example.com", display_name="O", role="user", is_local=True, username="o")
    db.add(other)
    await db.commit()
    await db.refresh(other)
    return other


async def test_other_users_scan_is_404_for_non_admin(db, user_client, monkeypatch, tmp_path):
    other = await _other_user(db)
    await _seed(db, tmp_path, FRONTS, user_id=other.id)
    fake = _patch(monkeypatch, tmp_path, BACKS)
    resp = await user_client.post(URL, json={})
    assert resp.status_code == 404 and fake.calls == []


async def test_admin_can_scan_backs_of_any_users_scan(
    db, admin_client, regular_user, monkeypatch, tmp_path
):
    await _seed(db, tmp_path, FRONTS, user_id=regular_user.id)
    _patch(monkeypatch, tmp_path, BACKS)
    assert (await admin_client.post(URL, json={})).status_code == 200


async def test_owner_can_scan_backs(db, user_client, regular_user, monkeypatch, tmp_path):
    await _seed(db, tmp_path, FRONTS, user_id=regular_user.id)
    _patch(monkeypatch, tmp_path, BACKS)
    assert (await user_client.post(URL, json={})).status_code == 200


async def _seed_scanner(db, config) -> Scanner:
    scanner = Scanner(name="s", device="test:device0", is_default=True, auto_deliver=True,
                      post_scan_config=config)
    db.add(scanner)
    await db.commit()
    await db.refresh(scanner)
    return scanner


async def test_auto_deliver_sends_the_merged_document(db, user_client, monkeypatch, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    scanner = await _seed_scanner(db, {"folder": str(out)})
    await _seed(db, tmp_path, FRONTS, scanner_id=scanner.id)
    _patch(monkeypatch, tmp_path, BACKS)
    resp = await user_client.post(URL, json={})
    merged_id = resp.json()["merged"]["scan_id"]
    delivered = os.listdir(out)
    assert len(delivered) == 1 and merged_id[:8] in delivered[0]
    assert len(tags(out / delivered[0])) == 6


async def test_auto_deliver_ocr_covers_merged_file(db, user_client, monkeypatch, tmp_path):
    from app.services.ocr_service import ocr_service

    scanner = await _seed_scanner(db, {"ocr": True})
    await _seed(db, tmp_path, FRONTS, scanner_id=scanner.id)
    _patch(monkeypatch, tmp_path, BACKS)
    seen = []

    async def fake_ocr(path, language="eng"):
        seen.append(path)

    monkeypatch.setattr(ocr_service, "apply_ocr", fake_ocr)
    merged_id = (await user_client.post(URL, json={})).json()["merged"]["scan_id"]
    assert seen == [(await _row(db, merged_id)).filepath]


async def test_no_delivery_without_auto_deliver(db, user_client, monkeypatch, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    scanner = await _seed_scanner(db, {"folder": str(out)})
    scanner.auto_deliver = False
    await db.commit()
    await _seed(db, tmp_path, FRONTS, scanner_id=scanner.id)
    _patch(monkeypatch, tmp_path, BACKS)
    assert (await user_client.post(URL, json={})).status_code == 200
    assert os.listdir(out) == []


async def test_merge_writes_audit_and_webhook(db, user_client, monkeypatch, tmp_path):
    from app.models import AuditEntry

    await _seed(db, tmp_path, FRONTS)
    _patch(monkeypatch, tmp_path, BACKS)
    events = []

    async def fake_dispatch(_db, event, payload):
        events.append((event, payload))

    monkeypatch.setattr(scanner_router, "dispatch_webhook", fake_dispatch)
    merged_id = (await user_client.post(URL, json={})).json()["merged"]["scan_id"]
    assert events == [("scan.complete", {"scan_id": merged_id, "format": "pdf", "pages": 6})]
    entries = (await db.execute(select(AuditEntry).where(AuditEntry.entity_id == merged_id))).all()
    assert len(entries) == 1


async def test_token_without_scan_permission_is_403(db, client, monkeypatch, tmp_path):
    await _seed(db, tmp_path, FRONTS)
    fake = _patch(monkeypatch, tmp_path, BACKS)
    user = User(email="p@example.com", display_name="P", role="user", is_local=True, username="p")
    db.add(user)
    await db.commit()
    await db.refresh(user)
    db.add(APIToken(user_id=user.id, name="t", token_hash=hash_token("pprs_backs_print_only"),
                    permissions=["print"]))
    await db.commit()
    resp = await client.post(
        URL, json={}, headers={"Authorization": "Bearer pprs_backs_print_only"},
    )
    assert resp.status_code == 403
    assert fake.calls == []


async def _delete_via_other_session(model, **where):
    async with async_session() as other:
        row = (await other.execute(select(model).filter_by(**where))).scalar_one()
        await other.delete(row)
        await other.commit()


async def _merge(user_client) -> str:
    resp = await user_client.post(URL, json={})
    assert resp.status_code == 200
    return resp.json()["merged"]["scan_id"]


async def test_backs_row_carries_source_and_is_refused(db, user_client, monkeypatch, tmp_path):
    await _seed(db, tmp_path, FRONTS)
    fake = _patch(monkeypatch, tmp_path, BACKS)
    body = (await user_client.post(URL, json={})).json()
    assert body["backs"]["duplex_source_scan_id"] == "front-scan-id"
    assert body["merged"]["duplex_source_scan_id"] == "front-scan-id"
    calls = len(fake.calls)
    resp = await user_client.post("/api/scanner/scans/backs-scan-id/backs", json={})
    assert resp.status_code == 400 and len(fake.calls) == calls


async def test_deleting_merged_scan_frees_fronts_for_redo(db, user_client, monkeypatch, tmp_path):
    await _seed(db, tmp_path, FRONTS)
    _patch(monkeypatch, tmp_path, BACKS)
    merged_id = await _merge(user_client)
    assert (await user_client.post(URL, json={})).status_code == 400
    assert (await user_client.delete(f"/api/scanner/scans/{merged_id}")).status_code == 204
    assert (await _row(db, "front-scan-id")).duplex_merged is False
    second = await user_client.post(URL, json={})
    assert second.status_code == 200 and second.json()["merged"]["scan_id"] != merged_id


async def test_bulk_deleting_merged_scan_frees_fronts(db, user_client, monkeypatch, tmp_path):
    await _seed(db, tmp_path, FRONTS)
    _patch(monkeypatch, tmp_path, BACKS)
    merged_id = await _merge(user_client)
    resp = await user_client.post("/api/scanner/scans/bulk-delete", json={"scan_ids": [merged_id]})
    assert resp.status_code == 200
    assert (await _row(db, "front-scan-id")).duplex_merged is False


async def test_deleting_backs_or_fronts_leaves_the_rest_alone(
    db, user_client, monkeypatch, tmp_path
):
    await _seed(db, tmp_path, FRONTS)
    _patch(monkeypatch, tmp_path, BACKS)
    merged_id = await _merge(user_client)
    assert (await user_client.delete("/api/scanner/scans/backs-scan-id")).status_code == 204
    assert (await _row(db, "front-scan-id")).duplex_merged is True
    assert (await user_client.delete("/api/scanner/scans/front-scan-id")).status_code == 204
    assert (await _row(db, merged_id)).duplex_merged is True
    # Deleting the merged scan once the fronts are gone must not fail.
    assert (await user_client.delete(f"/api/scanner/scans/{merged_id}")).status_code == 204


async def test_retention_frees_fronts_of_expired_merge(db, user_client, monkeypatch, tmp_path):
    from datetime import datetime, timedelta, timezone

    from app.services.retention_service import cleanup_old_scans

    await _seed(db, tmp_path, FRONTS)
    _patch(monkeypatch, tmp_path, BACKS)
    merged = await _row(db, await _merge(user_client))
    merged.created_at = datetime.now(timezone.utc) - timedelta(days=100)
    await db.commit()
    assert await cleanup_old_scans(db, 30) == 1
    assert (await _row(db, "front-scan-id")).duplex_merged is False


async def test_fronts_deleted_during_scan_keeps_backs(db, user_client, monkeypatch, tmp_path):
    await _seed(db, tmp_path, FRONTS)
    fake = _patch(monkeypatch, tmp_path, BACKS)

    async def hook():
        await _delete_via_other_session(ScanJob, scan_id="front-scan-id")

    fake.during_scan = hook
    resp = await user_client.post(URL, json={})
    assert resp.status_code == 409
    assert "unchanged" not in resp.json()["detail"]
    kept = await _row(db, "backs-scan-id")
    assert kept.status == "completed" and os.path.exists(kept.filepath)
    assert sorted(os.listdir(tmp_path)) == ["backs.pdf", "fronts.pdf"]
    assert len(await _all(db)) == 1


async def test_scanner_deleted_during_scan_does_not_lose_the_merge(
    db, user_client, monkeypatch, tmp_path
):
    scanner = await _seed_scanner(db, {})
    scanner.auto_deliver = False
    await db.commit()
    await _seed(db, tmp_path, FRONTS, scanner_id=scanner.id)
    fake = _patch(monkeypatch, tmp_path, BACKS)

    async def hook():
        await _delete_via_other_session(Scanner, name="s")

    fake.during_scan = hook
    body = (await user_client.post(URL, json={})).json()
    assert (await _row(db, body["merged"]["scan_id"])).scanner_id is None
    assert (await _row(db, "backs-scan-id")).scanner_id is None


async def test_scanner_deleted_and_mismatch_still_keeps_backs(
    db, user_client, monkeypatch, tmp_path
):
    scanner = await _seed_scanner(db, {})
    await _seed(db, tmp_path, FRONTS, scanner_id=scanner.id)
    fake = _patch(monkeypatch, tmp_path, [(11, "text")])

    async def hook():
        await _delete_via_other_session(Scanner, name="s")

    fake.during_scan = hook
    assert (await user_client.post(URL, json={})).status_code == 409
    assert (await _row(db, "backs-scan-id")).scanner_id is None


async def test_delivery_errors_do_not_turn_a_finished_merge_into_a_500(
    db, user_client, monkeypatch, tmp_path
):
    await _seed(db, tmp_path, FRONTS)
    _patch(monkeypatch, tmp_path, BACKS)

    async def boom(*a, **k):
        raise RuntimeError("webhook exploded")

    monkeypatch.setattr(scanner_router, "dispatch_webhook", boom)
    resp = await user_client.post(URL, json={})
    assert resp.status_code == 200 and resp.json()["merged"]["page_count"] == 6


async def test_merged_row_deleted_during_delivery_still_returns(
    db, user_client, monkeypatch, tmp_path
):
    scanner = await _seed_scanner(db, {"folder": str(tmp_path)})
    await _seed(db, tmp_path, FRONTS, scanner_id=scanner.id)
    _patch(monkeypatch, tmp_path, BACKS)

    async def delete_during_delivery(merged_row, *a, **k):
        await _delete_via_other_session(ScanJob, scan_id=merged_row.scan_id)
        os.unlink(merged_row.filepath)

    monkeypatch.setattr(scanner_router, "run_post_scan_actions", delete_during_delivery)
    resp = await user_client.post(URL, json={})
    assert resp.status_code == 200 and resp.json()["merged"]["page_count"] == 6
