"""Migration 015 adds scan_jobs.duplex_merged and downgrades cleanly."""
import asyncio
import uuid

import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from app.config import settings
from tests.test_migration_014 import _CONNECT_TIMEOUT_SECONDS, ALEMBIC_DIR, _maintenance_url


def _run(scratch_url, fn, revision):
    cfg = Config()
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    cfg.attributes["db_url"] = scratch_url
    return asyncio.to_thread(fn, cfg, revision)


async def _columns(url) -> list[str]:
    eng = create_async_engine(url)
    try:
        async with eng.connect() as conn:
            return list((await conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='scan_jobs'"
            ))).scalars().all())
    finally:
        await eng.dispose()


async def test_migration_015_up_and_down(migrated_db):
    maint = create_async_engine(_maintenance_url(), isolation_level="AUTOCOMMIT")
    name = f"papyrus_migration015_{uuid.uuid4().hex[:10]}"
    try:
        async with maint.connect() as conn:
            await asyncio.wait_for(
                conn.execute(text(f'CREATE DATABASE "{name}"')), _CONNECT_TIMEOUT_SECONDS
            )
    finally:
        await maint.dispose()
    url = sa.engine.url.make_url(settings.db_url).set(database=name).render_as_string(
        hide_password=False
    )
    try:
        await _run(url, command.upgrade, "014")
        eng = create_async_engine(url)
        try:
            async with eng.begin() as conn:
                await conn.execute(
                    text("INSERT INTO scan_jobs (scan_id, status) VALUES ('x', 'completed')")
                )
        finally:
            await eng.dispose()
        await _run(url, command.upgrade, "015")
        eng = create_async_engine(url)
        try:
            async with eng.connect() as conn:
                value = (
                    await conn.execute(text("SELECT duplex_merged FROM scan_jobs"))
                ).scalar_one()
                nullable = (await conn.execute(text(
                    "SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_name='scan_jobs' AND column_name='duplex_merged'"
                ))).scalar_one()
        finally:
            await eng.dispose()
        assert value is False and nullable == "NO"
        assert "duplex_source_scan_id" in await _columns(url)
        await _run(url, command.downgrade, "014")
        eng = create_async_engine(url)
        try:
            async with eng.connect() as conn:
                cols = (await conn.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='scan_jobs'"
                ))).scalars().all()
        finally:
            await eng.dispose()
        assert "duplex_merged" not in cols and "duplex_source_scan_id" not in cols
    finally:
        cleanup = create_async_engine(_maintenance_url(), isolation_level="AUTOCOMMIT")
        async with cleanup.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await cleanup.dispose()
