from sqlalchemy import inspect

from app.db import make_engine, migrate


def fresh_engine():
    return make_engine("sqlite://")


def test_models_and_migrations_match():
    """Fails when a model changes without a migration: run `alembic revision --autogenerate -m "..."`."""
    engine = fresh_engine()
    migrate.upgrade(engine)
    assert migrate.pending_model_changes(engine) == []


def test_downgrade_to_empty_and_back():
    engine = fresh_engine()
    migrate.upgrade(engine)
    tables = set(inspect(engine).get_table_names())
    assert {"agreements", "milestones", "evidence", "settlements", "alembic_version"} <= tables

    migrate.downgrade(engine, "base")
    assert set(inspect(engine).get_table_names()) == {"alembic_version"}

    migrate.upgrade(engine)
    assert set(inspect(engine).get_table_names()) == tables


def test_upgrade_is_idempotent():
    engine = fresh_engine()
    migrate.upgrade(engine)
    migrate.upgrade(engine)  # already at head: no-op, no error
