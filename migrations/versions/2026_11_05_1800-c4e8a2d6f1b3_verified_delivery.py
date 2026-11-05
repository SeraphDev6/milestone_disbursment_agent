"""verified delivery: evidence visibility, reveal at capture, skeptic pass

Revision ID: c4e8a2d6f1b3
Revises: b7d2f1a9c3e4
Create Date: 2026-11-05 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4e8a2d6f1b3"
down_revision: str | Sequence[str] | None = "b7d2f1a9c3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEVELS = ("hash_only", "verdict_only", "excerpt", "preview")


def _level(name: str) -> sa.Column:
    return sa.Column(
        name,
        sa.Enum(*LEVELS, name="evidencevisibility", native_enum=False, length=16),
        nullable=False,
        server_default="hash_only",
    )


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("agreements", schema=None) as batch_op:
        batch_op.add_column(_level("min_evidence_visibility"))
    with op.batch_alter_table("evidence", schema=None) as batch_op:
        batch_op.add_column(_level("payer_visibility"))
    with op.batch_alter_table("milestones", schema=None) as batch_op:
        batch_op.add_column(sa.Column("revealed_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("verdicts", schema=None) as batch_op:
        batch_op.add_column(sa.Column("skeptic", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("verdicts", schema=None) as batch_op:
        batch_op.drop_column("skeptic")
    with op.batch_alter_table("milestones", schema=None) as batch_op:
        batch_op.drop_column("revealed_at")
    with op.batch_alter_table("evidence", schema=None) as batch_op:
        batch_op.drop_column("payer_visibility")
    with op.batch_alter_table("agreements", schema=None) as batch_op:
        batch_op.drop_column("min_evidence_visibility")
