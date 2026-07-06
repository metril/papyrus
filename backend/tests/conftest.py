"""Shared pytest fixtures/setup.

The local dev venv does not have pycups installed (host lacks CUPS headers),
but Docker/CI images do. Stub the `cups` module so any code importing it is
still importable/testable locally.
"""
import sys
from unittest.mock import MagicMock

try:
    import cups  # noqa: F401
except ImportError:
    sys.modules["cups"] = MagicMock()

# --- Test environment wiring (MUST run before any `app.*` import) ----------
#
# `app.config` instantiates `settings` at import time and `app.database`
# builds the async engine from `settings.db_url` at import time. So the test
# DB URL has to be in the environment *before* those modules are imported.
# `PAPYRUS_TEST_DB_URL` (set by CI) wins; otherwise default to the local
# papyrus-test-pg container. We also set a throwaway Fernet key so the
# settings suite can encrypt/decrypt without a real deployment key.
import os

from cryptography.fernet import Fernet

os.environ["PAPYRUS_DB_URL"] = os.environ.get(
    "PAPYRUS_TEST_DB_URL",
    "postgresql+asyncpg://papyrus:papyrus@localhost:5433/papyrus_test",
)
os.environ.setdefault("PAPYRUS_ENCRYPTION_KEY", Fernet.generate_key().decode())

# --- Now `app.*` imports are safe ------------------------------------------
import asyncio
from pathlib import Path

import pytest
import pytest_asyncio
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from alembic import command
from app.auth.dependencies import get_current_user
from app.database import Base, async_session, engine
from app.main import app
from app.models import User
from app.services import settings_cache

BACKEND_DIR = Path(__file__).resolve().parent.parent
ALEMBIC_DIR = BACKEND_DIR / "alembic"

# Fixtures whose presence in a test means it needs the test Postgres. Requesting
# any of them auto-marks the test `integration` (see pytest_collection_modifyitems)
# and routes it through `migrated_db`, which skips cleanly if the DB is down.
_INTEGRATION_FIXTURES = {
    "migrated_db",
    "db",
    "client",
    "admin_user",
    "regular_user",
    "admin_client",
    "user_client",
}

_CONNECT_TIMEOUT_SECONDS = 5.0


def pytest_collection_modifyitems(config, items):
    """Auto-mark any test that uses a DB-backed fixture as `integration`."""
    for item in items:
        if _INTEGRATION_FIXTURES & set(getattr(item, "fixturenames", ())):
            item.add_marker("integration")


async def _probe_db() -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def migrated_db():
    """Session-scoped: bring the test DB schema to Alembic head exactly once.

    Skips the whole integration suite (with a clear reason) if the DB is
    unreachable. Drops and recreates the public schema first so reruns are
    deterministic, then runs `alembic upgrade head`. Alembic's env.py runs its
    own `asyncio.run`, so `command.upgrade` is invoked in a worker thread (no
    running loop there) via `asyncio.to_thread`.
    """
    try:
        await asyncio.wait_for(_probe_db(), timeout=_CONNECT_TIMEOUT_SECONDS)
    except Exception:
        await engine.dispose()
        pytest.skip("test Postgres unreachable — start papyrus-test-pg (see CLAUDE.md)")

    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))

    cfg = Config()
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    await asyncio.to_thread(command.upgrade, cfg, "head")

    # Drop connections opened on the session loop so per-test function-loop
    # fixtures never inherit a connection bound to a different event loop.
    await engine.dispose()
    yield
    await engine.dispose()


def _truncate_sql() -> str:
    tables = ", ".join(
        f'"{t.name}"' for t in Base.metadata.sorted_tables if t.name != "alembic_version"
    )
    return f"TRUNCATE {tables} RESTART IDENTITY CASCADE"


@pytest_asyncio.fixture(loop_scope="function")
async def db(migrated_db):
    """Function-scoped AsyncSession. Truncates every table after each test.

    Runs on the per-test (function) event loop so the session it yields — and
    anything a test does with it — shares one loop with the test body and the
    ASGI client. The engine is disposed on teardown so no pooled connection
    survives into the next test's loop.
    """
    session = async_session()
    try:
        yield session
    finally:
        await session.close()
        async with engine.begin() as conn:
            await conn.execute(text(_truncate_sql()))
        settings_cache.invalidate_all()
        await engine.dispose()


@pytest_asyncio.fixture(loop_scope="function")
async def client(db):
    """httpx AsyncClient bound to the ASGI app. Lifespan is intentionally NOT
    run (it needs CUPS + DB seeding + background loops); tests seed what they
    need via `db`. Clears dependency overrides after each test so a test that
    installs an override can't leak into the next one."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def _make_user(db, *, role: str, email: str, username: str) -> User:
    user = User(
        email=email,
        display_name=username.title(),
        role=role,
        is_local=True,
        username=username,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@pytest_asyncio.fixture(loop_scope="function")
async def admin_user(db) -> User:
    """A committed admin User row."""
    return await _make_user(db, role="admin", email="admin@example.com", username="admin")


@pytest_asyncio.fixture(loop_scope="function")
async def regular_user(db) -> User:
    """A committed non-admin User row (role='user')."""
    return await _make_user(db, role="user", email="user@example.com", username="user")


@pytest_asyncio.fixture(loop_scope="function")
async def admin_client(client, admin_user):
    """`client` with get_current_user overridden to the seeded admin user.

    Overriding get_current_user is sufficient for require_admin and
    require_permission(...) — both Depend on it and check user.role themselves.
    """
    app.dependency_overrides[get_current_user] = lambda: admin_user
    return client


@pytest_asyncio.fixture(loop_scope="function")
async def user_client(client, regular_user):
    """`client` with get_current_user overridden to the seeded regular user."""
    app.dependency_overrides[get_current_user] = lambda: regular_user
    return client
