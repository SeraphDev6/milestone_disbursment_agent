"""Engine, sessions and time helpers."""

from collections.abc import Iterator
from datetime import UTC, datetime

from fastapi import Request
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine

from app.models.columns import new_id, utcnow  # noqa: F401  (re-exported via app.db)


def as_utc(dt: datetime) -> datetime:
    """SQLite drops tzinfo on read; everything stored is UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def make_engine(url: str) -> Engine:
    if url in ("sqlite://", "sqlite:///:memory:"):
        # One shared in-memory database across threads (tests, throwaway demos).
        return create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False})
    return create_engine(url, pool_pre_ping=True)


def make_sessionmaker(engine: Engine, info: dict | None = None) -> sessionmaker[Session]:
    """`info` is copied into every session's `session.info` (the app puts its notifier there, so services can
    email people without carrying a mailer argument through every call)."""
    return sessionmaker(engine, class_=Session, expire_on_commit=False, info=dict(info or {}))


def get_session(request: Request) -> Iterator[Session]:
    factory: sessionmaker[Session] = request.app.state.sessionmaker
    with factory() as session:
        yield session
