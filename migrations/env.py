"""Alembic environment.

Uses a synchronous psycopg engine (the same driver the async app uses, just in sync mode) and
takes the database URL from application settings, so there is one source of truth for it. The
model metadata is imported so ``--autogenerate`` and drift checks see the whole schema.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from appointments_api.config import get_settings
from appointments_api.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Feed the URL from settings into Alembic's config at runtime.
config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Render SQL without a live connection (``alembic upgrade head --sql``)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
