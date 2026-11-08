"""Co-funded grants: one PayPal hold per funder (holds), tranche shares, lead funder, funding window

Revision ID: e3a7c0d5f9b4
Revises: d2f6b9c4e8a3
Create Date: 2026-11-12 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e3a7c0d5f9b4"
down_revision: str | Sequence[str] | None = "d2f6b9c4e8a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # The payer that created the agreement is its lead funder; co-funders join later with lead = false.
    with op.batch_alter_table("parties", schema=None) as batch_op:
        batch_op.add_column(sa.Column("lead", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("invited_email", sqlmodel.sql.sqltypes.AutoString(length=320), nullable=True))
    op.execute("UPDATE parties SET lead = true WHERE role = 'payer'")

    with op.batch_alter_table("agreements", schema=None) as batch_op:
        batch_op.add_column(sa.Column("funding_window_days", sa.Integer(), nullable=False, server_default="14"))

    op.create_table(
        "holds",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column("milestone_id", sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column("party_id", sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("paypal_order_id", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
        sa.Column("paypal_authorization_id", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
        sa.Column("funded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reauthorized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["milestone_id"], ["milestones.id"], name=op.f("fk_holds_milestone_id_milestones")),
        sa.ForeignKeyConstraint(["party_id"], ["parties.id"], name=op.f("fk_holds_party_id_parties")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_holds")),
    )
    op.create_index(op.f("ix_holds_milestone_id"), "holds", ["milestone_id"], unique=False)
    op.create_index(op.f("ix_holds_party_id"), "holds", ["party_id"], unique=False)
    op.create_index(op.f("ix_holds_paypal_authorization_id"), "holds", ["paypal_authorization_id"], unique=False)
    op.create_index(op.f("ix_holds_paypal_order_id"), "holds", ["paypal_order_id"], unique=False)

    # Every tranche funded so far had one payer and one hold: move it across (the hold borrows the tranche's id, which
    # is unique and lets settlements point at it without a join). Tranches never funded get no row.
    op.execute(
        "INSERT INTO holds (id, milestone_id, party_id, amount, paypal_order_id, paypal_authorization_id, funded_at, "
        "reauthorized_at, created_at) "
        "SELECT m.id, m.id, p.id, m.amount, m.paypal_order_id, m.paypal_authorization_id, m.funded_at, "
        "m.reauthorized_at, COALESCE(m.funded_at, CURRENT_TIMESTAMP) "
        "FROM milestones m JOIN parties p ON p.agreement_id = m.agreement_id AND p.role = 'payer' "
        "WHERE m.paypal_order_id IS NOT NULL OR m.paypal_authorization_id IS NOT NULL OR m.funded_at IS NOT NULL"
    )

    with op.batch_alter_table("settlements", schema=None) as batch_op:
        batch_op.add_column(sa.Column("hold_id", sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True))
        batch_op.create_index(batch_op.f("ix_settlements_hold_id"), ["hold_id"], unique=False)
        batch_op.create_foreign_key(batch_op.f("fk_settlements_hold_id_holds"), "holds", ["hold_id"], ["id"])
    op.execute(
        "UPDATE settlements SET hold_id = milestone_id WHERE mode = 'escrow_release' "
        "AND EXISTS (SELECT 1 FROM holds WHERE holds.id = settlements.milestone_id)"
    )

    with op.batch_alter_table("milestones", schema=None) as batch_op:
        batch_op.add_column(sa.Column("shares", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("proposed_amount", sa.Numeric(precision=14, scale=2), nullable=True))
        batch_op.drop_index(batch_op.f("ix_milestones_paypal_authorization_id"))
        batch_op.drop_index(batch_op.f("ix_milestones_paypal_order_id"))
        batch_op.drop_column("paypal_authorization_id")
        batch_op.drop_column("paypal_order_id")
        batch_op.drop_column("reauthorized_at")
    op.execute("UPDATE milestones SET shares = '[]' WHERE shares IS NULL")
    with op.batch_alter_table("milestones", schema=None) as batch_op:
        batch_op.alter_column("shares", existing_type=sa.JSON(), nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("milestones", schema=None) as batch_op:
        batch_op.add_column(sa.Column("reauthorized_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("paypal_order_id", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True))
        batch_op.add_column(
            sa.Column("paypal_authorization_id", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True)
        )
        batch_op.create_index(batch_op.f("ix_milestones_paypal_order_id"), ["paypal_order_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_milestones_paypal_authorization_id"), ["paypal_authorization_id"], unique=False
        )
        batch_op.drop_column("proposed_amount")
        batch_op.drop_column("shares")
    # One hold per tranche is all the old columns can carry: the latest live one, else the latest row.
    op.execute(
        "UPDATE milestones SET "
        "paypal_order_id = (SELECT paypal_order_id FROM holds WHERE holds.milestone_id = milestones.id "
        "ORDER BY paypal_authorization_id IS NULL, created_at DESC LIMIT 1), "
        "paypal_authorization_id = (SELECT paypal_authorization_id FROM holds WHERE holds.milestone_id = milestones.id "
        "ORDER BY paypal_authorization_id IS NULL, created_at DESC LIMIT 1), "
        "reauthorized_at = (SELECT reauthorized_at FROM holds WHERE holds.milestone_id = milestones.id "
        "ORDER BY paypal_authorization_id IS NULL, created_at DESC LIMIT 1)"
    )
    with op.batch_alter_table("settlements", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("fk_settlements_hold_id_holds"), type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_settlements_hold_id"))
        batch_op.drop_column("hold_id")
    op.drop_index(op.f("ix_holds_paypal_order_id"), table_name="holds")
    op.drop_index(op.f("ix_holds_paypal_authorization_id"), table_name="holds")
    op.drop_index(op.f("ix_holds_party_id"), table_name="holds")
    op.drop_index(op.f("ix_holds_milestone_id"), table_name="holds")
    op.drop_table("holds")
    with op.batch_alter_table("agreements", schema=None) as batch_op:
        batch_op.drop_column("funding_window_days")
    with op.batch_alter_table("parties", schema=None) as batch_op:
        batch_op.drop_column("invited_email")
        batch_op.drop_column("lead")
