"""Column helpers so table definitions stay one line per field."""

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import TypeAdapter
from sqlalchemy import JSON, DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy.types import TypeDecorator
from sqlmodel import Field, SQLModel

# Deterministic constraint names, so migrations can drop/alter them on every database (incl. SQLite batch mode).
# Must be set before any table is defined; every model module imports this one first.
SQLModel.metadata.naming_convention = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class PydanticJSON(TypeDecorator):
    """JSON column holding typed Pydantic values (e.g. list[Criterion]); validated on the way in and out."""

    impl = JSON
    cache_ok = True

    def __init__(self, tp: Any):
        super().__init__()
        self.tp = tp
        self.adapter = TypeAdapter(tp)

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return self.adapter.dump_python(self.adapter.validate_python(value), mode="json")

    def process_result_value(self, value, dialect):
        return None if value is None else self.adapter.validate_python(value)


def enum_type(enum: type[StrEnum], length: int = 32) -> SAEnum:
    """Stored as VARCHAR (no native DB enum, so adding a value needs no migration); loaded as the enum."""
    return SAEnum(
        enum,
        native_enum=False,
        create_constraint=False,
        length=length,
        values_callable=lambda e: [m.value for m in e],
        validate_strings=True,
    )


def id_field() -> Any:
    return Field(default_factory=new_id, primary_key=True, max_length=36)


def fk(target: str, *, optional: bool = False) -> Any:
    """Foreign key. Optional in Python (set when the row is attached through its relationship), but NOT NULL in
    the database unless `optional`."""
    return Field(default=None, foreign_key=target, index=True, max_length=36, nullable=optional)


def timestamp(*, optional: bool = False, description: str | None = None) -> Any:
    if optional:
        return Field(default=None, sa_type=DateTime(timezone=True), description=description)
    return Field(default_factory=utcnow, sa_type=DateTime(timezone=True), description=description)
