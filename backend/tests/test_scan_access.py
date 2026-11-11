"""Scan ownership (IDOR) rules across every route that takes a scan id."""
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.auth.dependencies import get_current_user
from app.auth.tokens import hash_token
from app.main import app
from app.models import APIToken, ScanJob, User

ROUTES = [
    ("get", "/api/v1/scans/{id}/download", None),
    ("get", "/api/v1/scans/{id}/thumbnail", None),
    ("post", "/api/v1/scans/{id}/email", {"to": "a@example.com"}),
    ("post", "/api/v1/scans/{id}/cloud?provider_id=1", None),
    ("post", "/api/v1/scans/{id}/paperless", None),
    ("post", "/api/v1/scans/{id}/ocr", None),
    ("post", "/api/v1/scans/{id}/enhance", {}),
    ("post", "/api/v1/scans/{id}/backs", {"rotate_backs": False}),
    ("post", "/api/v1/scans/{id}/smb?share_id=1", None),
    ("delete", "/api/v1/scans/{id}", None),
]


def _h(t):
    return {"Authorization": f"Bearer {t}"}


async def _user(db, name, role="user"):
    u = User(email=f"{name}@example.com", display_name=name, role=role,
             is_local=True, username=name)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _tok(db, user, plaintext, perms=("scan",)):
    db.add(APIToken(user_id=user.id, name="t", token_hash=hash_token(plaintext),
                    permissions=list(perms)))
    await db.commit()


@pytest_asyncio.fixture(loop_scope="function")
async def world(db):
    alice, bob = await _user(db, "alice"), await _user(db, "bob")
    admin = await _user(db, "adm", "admin")
    await _tok(db, alice, "pprs_alice")
    await _tok(db, bob, "pprs_bob")
    await _tok(db, admin, "pprs_admin_scan_only")
    await _tok(db, admin, "pprs_admin_full", ["scan", "admin"])
    s_alice = ScanJob(user_id=alice.id, status="scanning")
    s_net = ScanJob(user_id=None, status="scanning")
    db.add_all([s_alice, s_net])
    await db.commit()
    return alice, bob, admin, s_alice.scan_id, s_net.scan_id


@pytest.mark.parametrize("method,path,body", ROUTES)
async def test_other_users_token_gets_404(db, client, world, method, path, body):
    _, _, _, sid, _ = world
    r = await client.request(method, path.format(id=sid), json=body, headers=_h("pprs_bob"))
    assert r.status_code == 404, r.text
    assert r.json()["code"] == "not_found"


@pytest.mark.parametrize("tok", ["pprs_alice", "pprs_admin_full"])
@pytest.mark.parametrize("method,path,body", ROUTES)
async def test_owner_and_admin_pass_the_gate(db, client, world, method, path, body, tok):
    _, _, _, sid, _ = world
    r = await client.request(method, path.format(id=sid), json=body, headers=_h(tok))
    # past the ownership gate: anything but the not-found for a hidden scan
    assert not (r.status_code == 404 and r.json()["detail"] == "Scan not found"), r.text


async def test_admin_token_without_admin_permission_is_404(db, client, world):
    _, _, _, sid, _ = world
    r = await client.get(f"/api/v1/scans/{sid}/thumbnail", headers=_h("pprs_admin_scan_only"))
    assert r.status_code == 404 and r.json()["detail"] == "Scan not found"


async def test_null_owner_scan_session_yes_token_no(db, client, world):
    alice, _, _, _, net = world
    r = await client.get(f"/api/v1/scans/{net}/thumbnail", headers=_h("pprs_alice"))
    assert r.status_code == 404 and r.json()["detail"] == "Scan not found"
    r = await client.get(f"/api/v1/scans/{net}/thumbnail", headers=_h("pprs_admin_full"))
    assert r.status_code == 404 and r.json()["detail"] == "Scan not found"
    app.dependency_overrides[get_current_user] = lambda: alice  # session
    r = await client.get(f"/api/v1/scans/{net}/thumbnail")
    assert not (r.status_code == 404 and r.json()["detail"] == "Scan not found")


async def test_list_filters(db, client, world):
    alice, bob, admin, sid, net = world
    ids = lambda r: {i["scan_id"] for i in r.json()["items"]}  # noqa: E731
    r = await client.get("/api/v1/scans", headers=_h("pprs_alice"))
    assert ids(r) == {sid} and r.json()["total"] == 1
    assert ids(await client.get("/api/v1/scans", headers=_h("pprs_bob"))) == set()
    assert ids(await client.get("/api/v1/scans", headers=_h("pprs_admin_scan_only"))) == set()
    assert ids(await client.get("/api/v1/scans", headers=_h("pprs_admin_full"))) == {sid}
    app.dependency_overrides[get_current_user] = lambda: alice
    assert ids(await client.get("/api/v1/scans")) == {sid, net}
    assert ids(await client.get("/api/v1/scans?mine=true")) == {sid}
    app.dependency_overrides[get_current_user] = lambda: admin
    assert ids(await client.get("/api/v1/scans")) == {sid, net}
    assert ids(await client.get("/api/v1/scans?mine=true")) == set()


async def test_bulk_delete_ignores_inaccessible(db, client, world):
    _, _, _, sid, _ = world
    r = await client.post("/api/v1/scans/bulk-delete", json={"scan_ids": [sid]},
                          headers=_h("pprs_bob"))
    assert r.status_code == 200 and r.json()["deleted"] == 0
    assert (await db.execute(select(ScanJob).where(ScanJob.scan_id == sid))).scalar_one_or_none()
    r = await client.post("/api/v1/scans/bulk-delete", json={"scan_ids": [sid]},
                          headers=_h("pprs_alice"))
    assert r.json()["deleted"] == 1


async def test_collate_inaccessible_is_404(db, client, world):
    _, _, _, sid, _ = world
    r = await client.post("/api/v1/scans/collate", json={"scan_ids": [sid]},
                          headers=_h("pprs_bob"))
    assert r.status_code == 404


async def test_delete_by_owner_succeeds_other_leaves_row(db, client, world):
    _, _, _, sid, _ = world
    await client.delete(f"/api/v1/scans/{sid}", headers=_h("pprs_bob"))
    assert (await db.execute(select(ScanJob).where(ScanJob.scan_id == sid))).scalar_one_or_none()
    assert (await client.delete(f"/api/v1/scans/{sid}", headers=_h("pprs_alice"))
            ).status_code == 204
