"""agreements: reviewer_decides_withheld

Revision ID: b9d4f7a2e5c3
Revises: a8c3e6f1d4b2
Create Date: 2026-11-09 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b9d4f7a2e5c3"
down_revision: str | Sequence[str] | None = "a8c3e6f1d4b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("agreements", schema=None) as batch_op:
        # server_default so existing agreements get the new behaviour (on): withheld work is judged by a reviewer.
        batch_op.add_column(
            sa.Column("reviewer_decides_withheld", sa.Boolean(), nullable=False, server_default=sa.true())
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("agreements", schema=None) as batch_op:
        batch_op.drop_column("reviewer_decides_withheld")
