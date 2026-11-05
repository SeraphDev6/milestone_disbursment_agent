"""agreement deliverable_release

Revision ID: b7d2f1a9c3e4
Revises: a1c2e3f4b5d6
Create Date: 2026-11-05 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7d2f1a9c3e4"
down_revision: str | Sequence[str] | None = "a1c2e3f4b5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("agreements", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "deliverable_release",
                sa.Enum("on_submit", "on_capture", name="deliverablerelease", native_enum=False, length=16),
                nullable=False,
                server_default="on_submit",
            )
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("agreements", schema=None) as batch_op:
        batch_op.drop_column("deliverable_release")
