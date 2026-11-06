"""audit_events: hash chain (prev_hash, hash)

Revision ID: c1e5a8b3d7f2
Revises: b9d4f7a2e5c3
Create Date: 2026-11-10 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1e5a8b3d7f2"
down_revision: str | Sequence[str] | None = "b9d4f7a2e5c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        # Nullable: rows from before the chain stay unsealed until `python -m app.jobs seal-audit` hashes them.
        batch_op.add_column(sa.Column("prev_hash", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True))
        batch_op.add_column(sa.Column("hash", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True))
        batch_op.create_index(batch_op.f("ix_audit_events_hash"), ["hash"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_audit_events_hash"))
        batch_op.drop_column("hash")
        batch_op.drop_column("prev_hash")
