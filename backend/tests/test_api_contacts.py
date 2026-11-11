"""Address book CRUD and permissions."""
C = "/api/v1/contacts"


async def test_crud_and_validation(admin_client):
    r = await admin_client.post(C, json={"name": "Bob", "email": "bob@example.com"})
    assert r.status_code == 201
    cid = r.json()["id"]
    assert (await admin_client.post(C, json={"name": "X", "email": "nope"})).status_code == 422
    r = await admin_client.put(f"{C}/{cid}", json={"name": "Bobby", "email": "b@example.org"})
    assert r.json()["name"] == "Bobby"
    assert [c["id"] for c in (await admin_client.get(C)).json()] == [cid]
    assert (await admin_client.delete(f"{C}/{cid}")).status_code == 204
    assert (await admin_client.delete(f"{C}/{cid}")).status_code == 404


async def test_non_admin_can_list_not_write(user_client):
    assert (await user_client.get(C)).status_code == 200
    assert (await user_client.post(C, json={"name": "a", "email": "a@b.co"})).status_code == 403


async def test_print_only_token_cannot_list(client, db, regular_user):
    from app.auth.tokens import hash_token
    from app.models import APIToken
    db.add(APIToken(user_id=regular_user.id, name="p", token_hash=hash_token("pprs_po"),
                    permissions=["print"]))
    db.add(APIToken(user_id=regular_user.id, name="s", token_hash=hash_token("pprs_sc"),
                    permissions=["scan"]))
    await db.commit()
    assert (await client.get(C, headers={"Authorization": "Bearer pprs_po"})).status_code == 403
    assert (await client.get(C, headers={"Authorization": "Bearer pprs_sc"})).status_code == 200
