"""Regression test for migration 014's default-printer/scanner cleanup.

Reproduces the pre-fix failure: the `ux_printers_default`/`ux_scanners_default`
partial unique indexes had no data-cleanup step ahead of them, so an install
that already has two rows with `is_default=true` (possible under the pre-014
`set_default_printer` clear-then-set race) failed `create_index` with
`IntegrityError: could not create unique index ... Key (is_default)=(t) is
duplicated`.

Runs against a disposable scratch database that this test creates and drops
itself, rather than the shared `papyrus_test` database `migrated_db` manages
in conftest.py -- it needs to stop at revision 013, seed bad data, then
continue to head, independent of the rest of the suite. Creating/dropping the
scratch database via a plain SQL connection (not `docker exec`) is
deliberate: this must also pass against the CI Postgres *service* container,
which isn't reachable via `docker exec`.
"""
import asyncio
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from app.config import settings

BACKEND_DIR = Path(__file__).resolve().parent.parent
ALEMBIC_DIR = BACKEND_DIR / "alembic"

_CONNECT_TIMEOUT_SECONDS = 5.0


def _maintenance_url() -> str:
    """The same server as PAPYRUS_DB_URL, but the always-present `postgres`
    maintenance database instead of the app's own database -- CREATE/DROP
    DATABASE can't run against the database being created/dropped.

    Uses render_as_string(hide_password=False): plain str(url) masks the
    password (renders `***`), which would make every connection attempt
    below fail auth."""
    return sa.engine.url.make_url(settings.db_url).set(
        database="postgres"
    ).render_as_string(hide_password=False)


async def _run_alembic_upgrade(scratch_url: str, revision: str) -> None:
    """Point the app's settings.db_url at the scratch database for the
    duration of one alembic command. env.py reads settings.db_url fresh on
    every invocation (`from app.config import settings`, same singleton), so
    mutating the attribute is enough -- it does not touch app.database's
    already-constructed engine, which stays bound to the real test DB."""
    original = settings.db_url
    settings.db_url = scratch_url
    try:
        cfg = Config()
        cfg.set_main_option("script_location", str(ALEMBIC_DIR))
        await asyncio.to_thread(command.upgrade, cfg, revision)
    finally:
        settings.db_url = original


@pytest.mark.integration
async def test_migration_014_dedupes_existing_default_printers_and_scanners():
    """Seed two is_default=true printers and two is_default=true scanners at
    revision 013, then upgrade to head. Must succeed (not IntegrityError) and
    leave exactly the lowest-id row of each table as the survivor."""
    maint_engine = create_async_engine(_maintenance_url(), isolation_level="AUTOCOMMIT")
    db_name = f"papyrus_migration014_{uuid.uuid4().hex[:10]}"
    try:
        async with maint_engine.connect() as conn:
            await asyncio.wait_for(
                conn.execute(text(f'CREATE DATABASE "{db_name}"')),
                timeout=_CONNECT_TIMEOUT_SECONDS,
            )
    except Exception:
        await maint_engine.dispose()
        pytest.skip("test Postgres unreachable — start papyrus-test-pg (see CLAUDE.md)")
    await maint_engine.dispose()

    scratch_url = sa.engine.url.make_url(settings.db_url).set(
        database=db_name
    ).render_as_string(hide_password=False)
    try:
        await _run_alembic_upgrade(scratch_url, "013")

        scratch_engine = create_async_engine(scratch_url)
        try:
            async with scratch_engine.begin() as conn:
                result = await conn.execute(
                    text(
                        "INSERT INTO printers "
                        "(display_name, cups_name, uri, is_default, is_network_queue, "
                        "auto_release) VALUES "
                        "('Printer 1', 'printer1', '', true, false, false), "
                        "('Printer 2', 'printer2', '', true, false, false) "
                        "RETURNING id"
                    )
                )
                printer_ids = sorted(row[0] for row in result)

                result = await conn.execute(
                    text(
                        "INSERT INTO scanners (name, device, is_default, auto_deliver) "
                        "VALUES ('Scanner 1', 'dev1', true, false), "
                        "('Scanner 2', 'dev2', true, false) "
                        "RETURNING id"
                    )
                )
                scanner_ids = sorted(row[0] for row in result)
        finally:
            await scratch_engine.dispose()

        assert len(printer_ids) == 2 and len(scanner_ids) == 2

        # This must not raise. Pre-fix, create_index on ux_printers_default
        # (and ux_scanners_default) raised IntegrityError here.
        await _run_alembic_upgrade(scratch_url, "014")

        scratch_engine = create_async_engine(scratch_url)
        try:
            async with scratch_engine.connect() as conn:
                printer_rows = (
                    await conn.execute(text("SELECT id, is_default FROM printers ORDER BY id"))
                ).all()
                scanner_rows = (
                    await conn.execute(text("SELECT id, is_default FROM scanners ORDER BY id"))
                ).all()
        finally:
            await scratch_engine.dispose()

        printer_defaults = [row.id for row in printer_rows if row.is_default]
        scanner_defaults = [row.id for row in scanner_rows if row.is_default]
        assert printer_defaults == [printer_ids[0]]
        assert scanner_defaults == [scanner_ids[0]]
    finally:
        cleanup_engine = create_async_engine(_maintenance_url(), isolation_level="AUTOCOMMIT")
        async with cleanup_engine.connect() as conn:
            # Drop any lingering connections (e.g. an alembic engine that
            # hasn't finished disposing yet) so DROP DATABASE doesn't fail
            # with "database is being accessed by other users".
            await conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": db_name},
            )
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        await cleanup_engine.dispose()
