"""contractor-owned repos: integration mode, handover and repo id, github_handovers

Revision ID: e6a1c4f8b2d9
Revises: d5f9b3e7a2c1
Create Date: 2026-11-06 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e6a1c4f8b2d9"
down_revision: str | Sequence[str] | None = "d5f9b3e7a2c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("integrations", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("mode", sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False, server_default="merged_pr")
        )
        batch_op.add_column(sa.Column("handover", sqlmodel.sql.sqltypes.AutoString(length=16), nullable=True))
        batch_op.add_column(sa.Column("handover_repo", sqlmodel.sql.sqltypes.AutoString(length=200), nullable=True))
        batch_op.add_column(sa.Column("repo_id", sa.BigInteger(), nullable=True))

    op.create_table(
        "github_handovers",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column("milestone_id", sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column("evidence_id", sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column("integration_id", sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column("method", sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False),
        sa.Column("source_repo", sqlmodel.sql.sqltypes.AutoString(length=200), nullable=False),
        sa.Column("repo_id", sa.BigInteger(), nullable=True),
        sa.Column("pinned_sha", sqlmodel.sql.sqltypes.AutoString(length=40), nullable=False),
        sa.Column("target_repo", sqlmodel.sql.sqltypes.AutoString(length=200), nullable=True),
        sa.Column("transferred_to", sqlmodel.sql.sqltypes.AutoString(length=200), nullable=True),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tree_sha", sqlmodel.sql.sqltypes.AutoString(length=40), nullable=True),
        sa.Column("pr_url", sqlmodel.sql.sqltypes.AutoString(length=500), nullable=True),
        sa.Column("file_id", sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True),
        sa.Column("error", sqlmodel.sql.sqltypes.AutoString(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["evidence_id"], ["evidence.id"], name=op.f("fk_github_handovers_evidence_id_evidence")
        ),
        sa.ForeignKeyConstraint(["file_id"], ["files.id"], name=op.f("fk_github_handovers_file_id_files")),
        sa.ForeignKeyConstraint(
            ["integration_id"], ["integrations.id"], name=op.f("fk_github_handovers_integration_id_integrations")
        ),
        sa.ForeignKeyConstraint(
            ["milestone_id"], ["milestones.id"], name=op.f("fk_github_handovers_milestone_id_milestones")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_github_handovers")),
        sa.UniqueConstraint("milestone_id", "evidence_id", name=op.f("uq_github_handovers_milestone_id_evidence_id")),
    )
    with op.batch_alter_table("github_handovers", schema=None) as batch_op:
        for col in ("milestone_id", "evidence_id", "integration_id", "file_id", "status"):
            batch_op.create_index(batch_op.f(f"ix_github_handovers_{col}"), [col], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("github_handovers", schema=None) as batch_op:
        for col in ("milestone_id", "evidence_id", "integration_id", "file_id", "status"):
            batch_op.drop_index(batch_op.f(f"ix_github_handovers_{col}"))
    op.drop_table("github_handovers")
    with op.batch_alter_table("integrations", schema=None) as batch_op:
        batch_op.drop_column("repo_id")
        batch_op.drop_column("handover_repo")
        batch_op.drop_column("handover")
        batch_op.drop_column("mode")
