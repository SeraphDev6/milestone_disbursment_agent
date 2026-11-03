"""Alembic environment. Autogenerates from SQLModel.metadata, so app/models stays the single source of truth.

Connection, in order of preference:
1. config.attributes["connection"] (the app and tests migrate in-process, incl. in-memory SQLite)
2. the app's settings (DATABASE_URL / .env), for the `alembic` CLI
"""

from alembic import context
from sqlmodel import SQLModel

import app.models  # noqa: F401  (registers every table on SQLModel.metadata)
from app.config import Settings
from app.db.migrate import render_item
from app.db.session import make_engine

config = context.config
target_metadata = SQLModel.metadata


def _configure(**kwargs) -> None:
    context.configure(
        target_metadata=target_metadata,
        compare_type=True,
        render_item=render_item,
        user_module_prefix="sqlmodel.sql.sqltypes.",
        **kwargs,
    )


def run_migrations_offline() -> None:
    _configure(url=Settings().database_url, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_with(connection) -> None:
    # Batch mode everywhere: SQLite can't ALTER most things (batch rebuilds the table), and on Postgres batch
    # operations are plain ALTERs, so one migration file works on both.
    _configure(connection=connection, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        run_with(connection)
        return
    engine = make_engine(Settings().database_url)
    with engine.connect() as conn:
        run_with(conn)
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
