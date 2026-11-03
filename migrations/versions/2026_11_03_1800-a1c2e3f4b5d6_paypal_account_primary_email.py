"""paypal account primary email

Revision ID: a1c2e3f4b5d6
Revises: 0d0407c03211
Create Date: 2026-11-03 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1c2e3f4b5d6"
down_revision: str | Sequence[str] | None = "0d0407c03211"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("paypal_accounts", schema=None) as batch_op:
        batch_op.add_column(sa.Column("primary_email", sqlmodel.sql.sqltypes.AutoString(length=320), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("paypal_accounts", schema=None) as batch_op:
        batch_op.drop_column("primary_email")
