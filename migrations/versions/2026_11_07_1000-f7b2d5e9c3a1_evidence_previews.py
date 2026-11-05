"""evidence previews: derived preview file, payee preview link and its expiry

Revision ID: f7b2d5e9c3a1
Revises: e6a1c4f8b2d9
Create Date: 2026-11-07 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f7b2d5e9c3a1"
down_revision: str | Sequence[str] | None = "e6a1c4f8b2d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("evidence", schema=None) as batch_op:
        batch_op.add_column(sa.Column("preview_url", sqlmodel.sql.sqltypes.AutoString(length=500), nullable=True))
        batch_op.add_column(sa.Column("preview_expires_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("preview_file_id", sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True))
        batch_op.create_index(batch_op.f("ix_evidence_preview_file_id"), ["preview_file_id"], unique=False)
        batch_op.create_foreign_key(
            batch_op.f("fk_evidence_preview_file_id_files"), "files", ["preview_file_id"], ["id"]
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("evidence", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("fk_evidence_preview_file_id_files"), type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_evidence_preview_file_id"))
        batch_op.drop_column("preview_file_id")
        batch_op.drop_column("preview_expires_at")
        batch_op.drop_column("preview_url")
