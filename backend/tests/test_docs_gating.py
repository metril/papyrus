"""/docs, /redoc, /openapi.json require auth unless public_api_docs is on."""
import pytest

from app.auth.tokens import generate_token
from app.models import APIToken, AppConfig, User
from app.services import settings_cache

PATHS = ["/docs", "/redoc", "/openapi.json"]


@pytest.mark.parametrize("path", PATHS)
async def test_unauthenticated_gets_401_envelope(client, path):
    r = await client.get(path)
    assert r.status_code == 401
    assert r.json()["code"] == "unauthorized"


@pytest.mark.parametrize("path", PATHS)
async def test_authenticated_user_allowed(client, db, path):
    user = User(email="d@example.com", display_name="D", role="user", is_local=True, username="d")
    db.add(user)
    await db.commit()
    await db.refresh(user)
    plaintext, token_hash = generate_token()
    db.add(APIToken(user_id=user.id, name="t", token_hash=token_hash, permissions=["print"]))
    await db.commit()
    r = await client.get(path, headers={"Authorization": f"Bearer {plaintext}"})
    assert r.status_code == 200


async def test_public_setting_opens_docs(client, db):
    db.add(AppConfig(key="public_api_docs", value="true"))
    await db.commit()
    settings_cache.invalidate("public_api_docs")
    for path in PATHS:
        assert (await client.get(path)).status_code == 200
    assert "openapi" in (await client.get("/openapi.json")).json()
