"""Run Alembic migrations in-process, on an existing engine (works for in-memory SQLite too)."""

from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


def render_item(type_: str, obj, autogen_context):
    """Render our custom column types as their portable equivalents in migration files (used by env.py too)."""
    from app.models.columns import PydanticJSON

    if type_ == "type" and isinstance(obj, PydanticJSON):
        return "sa.JSON()"
    return False


def _config(connection) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes["connection"] = connection
    return cfg


def upgrade(engine: Engine, revision: str = "head") -> None:
    with engine.begin() as conn:
        command.upgrade(_config(conn), revision)


def downgrade(engine: Engine, revision: str) -> None:
    with engine.begin() as conn:
        command.downgrade(_config(conn), revision)


def pending_model_changes(engine: Engine) -> list:
    """Differences between app/models and the migrated database. Empty means every model change has a migration."""
    import app.models  # noqa: F401

    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True, "render_item": render_item})
        return compare_metadata(ctx, SQLModel.metadata)
