"""Personal address book: CRUD, owner isolation, permissions, cascade."""
from sqlalchemy import select

from app.auth.dependencies import get_current_user
from app.auth.tokens import hash_token
from app.main import app
from app.models import APIToken, Contact, User

C = "/api/v1/contacts"


def _h(t):
    return {"Authorization": f"Bearer {t}"}


async def test_crud_and_validation(user_client):
    r = await user_client.post(C, json={"name": "Bob", "email": "bob@example.com"})
    assert r.status_code == 201
    assert set(r.json()) >= {"id", "name", "email", "created_at", "updated_at"}
    cid = r.json()["id"]
    assert (await user_client.post(C, json={"name": "X", "email": "nope"})).status_code == 422
    r = await user_client.put(f"{C}/{cid}", json={"name": "Bobby", "email": "b@example.org"})
    assert r.json()["name"] == "Bobby"
    assert [c["id"] for c in (await user_client.get(C)).json()] == [cid]
    assert (await user_client.delete(f"{C}/{cid}")).status_code == 204
    assert (await user_client.delete(f"{C}/{cid}")).status_code == 404


async def test_owner_isolation_no_admin_override(client, db, admin_user, regular_user):
    app.dependency_overrides[get_current_user] = lambda: regular_user
    cid = (await client.post(C, json={"name": "A", "email": "a@example.com"})).json()["id"]
    for other in (admin_user, await _other(db)):
        app.dependency_overrides[get_current_user] = lambda o=other: o
        assert (await client.get(C)).json() == []
        body = {"name": "Z", "email": "z@example.com"}
        assert (await client.put(f"{C}/{cid}", json=body)).status_code == 404
        assert (await client.delete(f"{C}/{cid}")).status_code == 404
    app.dependency_overrides[get_current_user] = lambda: regular_user
    assert [c["name"] for c in (await client.get(C)).json()] == ["A"]


async def _other(db):
    u = User(email="b@example.com", display_name="B", role="user", is_local=True, username="b")
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def test_token_permissions(client, db, regular_user):
    db.add(APIToken(user_id=regular_user.id, name="p", token_hash=hash_token("pprs_po"),
                    permissions=["print"]))
    db.add(APIToken(user_id=regular_user.id, name="s", token_hash=hash_token("pprs_sc"),
                    permissions=["scan"]))
    await db.commit()
    po, sc = _h("pprs_po"), _h("pprs_sc")
    body = {"name": "a", "email": "a@b.co"}
    cid = (await client.post(C, json=body, headers=sc)).json()["id"]
    fake = "00000000-0000-0000-0000-000000000000"
    assert (await client.get(C, headers=po)).status_code == 403
    assert (await client.post(C, json=body, headers=po)).status_code == 403
    assert (await client.put(f"{C}/{fake}", json=body, headers=po)).status_code == 403
    assert (await client.delete(f"{C}/{fake}", headers=po)).status_code == 403
    assert (await client.get(C, headers=sc)).json()[0]["id"] == cid
    assert (await client.put(f"{C}/{cid}", json={"name": "b", "email": "b@b.co"}, headers=sc)
            ).status_code == 200
    assert (await client.delete(f"{C}/{cid}", headers=sc)).status_code == 204


async def test_contacts_cascade_on_user_delete(admin_client, db, regular_user):
    db.add(Contact(owner_id=regular_user.id, name="n", email="n@example.com"))
    await db.commit()
    assert (await admin_client.delete(f"/api/v1/admin/users/{regular_user.id}")).status_code == 204
    db.expire_all()
    assert (await db.execute(select(Contact))).scalars().all() == []
