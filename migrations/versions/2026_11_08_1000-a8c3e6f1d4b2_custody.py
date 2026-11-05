"""custody: purged files, object storage keys, tranche custody end

Revision ID: a8c3e6f1d4b2
Revises: f7b2d5e9c3a1
Create Date: 2026-11-08 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a8c3e6f1d4b2"
down_revision: str | Sequence[str] | None = "f7b2d5e9c3a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("files", schema=None) as batch_op:
        batch_op.add_column(sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("storage_key", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True))
    with op.batch_alter_table("milestones", schema=None) as batch_op:
        batch_op.add_column(sa.Column("custody_ended_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("milestones", schema=None) as batch_op:
        batch_op.drop_column("custody_ended_at")
    with op.batch_alter_table("files", schema=None) as batch_op:
        batch_op.drop_column("storage_key")
        batch_op.drop_column("purged_at")
