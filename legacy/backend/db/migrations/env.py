"""Alembic env.py — Central DSP.

Supports both online (direct DB) and offline (SQL script) modes.
Database URL is taken from DATABASE_URL environment variable.
"""
import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# ── Make backend/ importable ───────────────────────────────────────────────
BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from backend.database import Base          # noqa: E402  — SQLAlchemy Base
import backend.models  # noqa: E402, F401  — registers all models with Base

# ── Alembic config ─────────────────────────────────────────────────────────
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    """Read DATABASE_URL from environment; fallback to alembic.ini value."""
    url = os.environ.get("DATABASE_URL")
    if url:
        # SQLAlchemy 1.4+ requires 'postgresql+psycopg2://' instead of 'postgres://'
        return url.replace("postgres://", "postgresql+psycopg2://", 1)
    return config.get_main_option("sqlalchemy.url")


def run_migrations_offline() -> None:
    """Run migrations without a live DB connection — emits SQL to stdout."""
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations with a live DB connection."""
    configuration = config.get_section(config.config_ini_section)
    configuration["sqlalchemy.url"] = get_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,        # detect column type changes
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
