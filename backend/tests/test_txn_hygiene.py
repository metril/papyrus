"""No DB transaction may sit open across slow external I/O."""
from sqlalchemy import text

from app.database import engine
from app.models import Scanner
from app.routers import scanner as scanner_router
from app.services import alert_service
from tests.test_alert_service import _FakeDB, _printer  # noqa: F401


async def _idle_in_txn() -> int:
    async with engine.connect() as conn:
        r = await conn.execute(text(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
            " AND pid <> pg_backend_pid() AND state LIKE 'idle in transaction%'"
        ))
        return r.scalar_one()


async def test_scan_holds_no_transaction_during_scan_and_delivery(
    user_client, db, monkeypatch, tmp_path
):
    f = tmp_path / "s.png"
    f.write_bytes(b"x")
    seen: dict[str, int] = {}

    class FakeScan:
        async def scan(self, **kw):
            seen["scan"] = await _idle_in_txn()
            return "id", str(f)

    async def fake_actions(*a, **kw):
        seen["post"] = await _idle_in_txn()

    async def fake_device(_db):
        return "test:dev"

    db.add(Scanner(name="s", device="test:dev", is_default=True, auto_deliver=True,
                   post_scan_config={"folder": "/tmp"}))
    await db.commit()
    monkeypatch.setattr(scanner_router, "scan_service", FakeScan())
    monkeypatch.setattr(scanner_router, "get_default_scanner_device", fake_device)
    monkeypatch.setattr(scanner_router, "run_post_scan_actions", fake_actions)

    resp = await user_client.post("/api/scanner/scan", json={})
    assert resp.status_code == 201, resp.text
    assert seen == {"scan": 0, "post": 0}


async def test_post_scan_actions_hold_no_transaction_during_upload(db, monkeypatch, tmp_path):
    from types import SimpleNamespace

    from app.services import scan_service as ss

    f = tmp_path / "s.pdf"
    f.write_bytes(b"x")
    seen = {}

    async def fake_copy(*a, **kw):
        seen["n"] = await _idle_in_txn()

    monkeypatch.setattr(ss.shutil, "copy2", lambda *a, **k: None)
    # Open a transaction the way a caller would have, then deliver.
    await db.execute(text("select 1"))
    assert db.in_transaction()
    import asyncio

    real = asyncio.to_thread

    async def spy(fn, *a, **k):
        seen["n"] = await _idle_in_txn()
        return await real(fn, *a, **k)

    monkeypatch.setattr(ss.asyncio, "to_thread", spy)
    job = SimpleNamespace(filepath=str(f), format="pdf", scan_id="x", error_message=None,
                          resolution=300, mode="Color", page_count=1)
    scanner = SimpleNamespace(post_scan_config={"folder": str(tmp_path)})
    try:
        await ss.run_post_scan_actions(job, scanner, db)
    except Exception:
        pass
    assert seen.get("n") == 0


async def test_check_alerts_commits_before_probes(monkeypatch):
    class DB(_FakeDB):
        txn = True

        async def commit(self):
            self.txn = False
            await super().commit()

    db = DB([_printer()])
    seen = {}

    async def reachable():
        seen["txn"] = db.txn
        return False

    async def fake_get_setting(_db, key):
        return {"alerts_enabled": "true"}.get(key)

    import app.routers.settings as st

    monkeypatch.setattr(st, "get_setting", fake_get_setting)
    monkeypatch.setattr(alert_service, "_cups_reachable", reachable)
    await alert_service.check_alerts(db)
    assert seen["txn"] is False
