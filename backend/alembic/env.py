import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.config import settings
from app.database import Base
from app.models import (  # noqa: F401 - ensure all models are imported for autogenerate
    APIToken,
    AppConfig,
    CloudProvider,
    PrintJob,
    ScanJob,
    SMBShare,
    User,
)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Override sqlalchemy.url with the app's configured database URL. A caller
# that built this Config programmatically (rather than from alembic.ini) can
# route around the process-global `settings.db_url` by stashing a URL in
# `config.attributes["db_url"]` -- this is alembic's documented in-process
# override channel and is never populated by alembic.ini, so the normal
# CLI/entrypoint path (`python -m alembic upgrade head`) is unaffected.
url = config.attributes.get("db_url") or settings.db_url
config.set_main_option("sqlalchemy.url", url)


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
