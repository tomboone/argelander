"""Alembic environment: the URL comes from Settings (or the caller), never from a config file."""

from alembic import context
from sqlalchemy import create_engine, pool

from argelander.config import get_settings
from argelander.db.models import Base

config = context.config
target_metadata = Base.metadata


def database_url() -> str:
    # `argelander.db.migrations.alembic_config(url)` passes an explicit URL (the tests use it);
    # otherwise this is DATABASE_URL, as for every other service.
    return config.attributes.get("database_url") or get_settings().database_url


def run_migrations_offline() -> None:
    """Emit SQL to stdout (`alembic upgrade head --sql`) without connecting."""
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
