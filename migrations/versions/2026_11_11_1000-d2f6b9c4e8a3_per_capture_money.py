"""settlements and disbursements carry their own amount (per-capture money, partial release); agreements.partial_release

Revision ID: d2f6b9c4e8a3
Revises: c1e5a8b3d7f2
Create Date: 2026-11-11 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d2f6b9c4e8a3"
down_revision: str | Sequence[str] | None = "c1e5a8b3d7f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("agreements", schema=None) as batch_op:
        batch_op.add_column(sa.Column("partial_release", sa.Boolean(), nullable=False, server_default=sa.false()))

    # Every settlement and disbursement so far moved the whole tranche: backfill the amount from the milestone.
    with op.batch_alter_table("settlements", schema=None) as batch_op:
        batch_op.add_column(sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=True))
        batch_op.add_column(sa.Column("final", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch_op.add_column(sa.Column("criterion_ids", sa.JSON(), nullable=True))
    op.execute(
        "UPDATE settlements SET amount = (SELECT amount FROM milestones WHERE milestones.id = settlements.milestone_id)"
    )
    op.execute("UPDATE settlements SET criterion_ids = '[]' WHERE criterion_ids IS NULL")
    with op.batch_alter_table("settlements", schema=None) as batch_op:
        batch_op.alter_column("amount", existing_type=sa.Numeric(precision=14, scale=2), nullable=False)
        batch_op.alter_column("criterion_ids", existing_type=sa.JSON(), nullable=False)

    with op.batch_alter_table("disbursements", schema=None) as batch_op:
        batch_op.add_column(sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=True))
        batch_op.add_column(sa.Column("settlement_id", sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True))
        batch_op.create_index(batch_op.f("ix_disbursements_settlement_id"), ["settlement_id"], unique=False)
        batch_op.create_foreign_key(
            batch_op.f("fk_disbursements_settlement_id_settlements"), "settlements", ["settlement_id"], ["id"]
        )
    op.execute(
        "UPDATE disbursements SET amount = "
        "(SELECT amount FROM milestones WHERE milestones.id = disbursements.milestone_id)"
    )
    # A disbursement moves the money of the tranche's completed capture (there was one per tranche).
    op.execute(
        "UPDATE disbursements SET settlement_id = ("
        "SELECT id FROM settlements WHERE settlements.milestone_id = disbursements.milestone_id "
        "AND settlements.status = 'succeeded' AND settlements.paypal_ref IS NOT NULL "
        "ORDER BY settlements.attempt DESC LIMIT 1)"
    )
    with op.batch_alter_table("disbursements", schema=None) as batch_op:
        batch_op.alter_column("amount", existing_type=sa.Numeric(precision=14, scale=2), nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("disbursements", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("fk_disbursements_settlement_id_settlements"), type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_disbursements_settlement_id"))
        batch_op.drop_column("settlement_id")
        batch_op.drop_column("amount")
    with op.batch_alter_table("settlements", schema=None) as batch_op:
        batch_op.drop_column("criterion_ids")
        batch_op.drop_column("final")
        batch_op.drop_column("amount")
    with op.batch_alter_table("agreements", schema=None) as batch_op:
        batch_op.drop_column("partial_release")
