"""github app: installations, check runs, integrations via the app

Revision ID: d5f9b3e7a2c1
Revises: c4e8a2d6f1b3
Create Date: 2026-11-06 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5f9b3e7a2c1"
down_revision: str | Sequence[str] | None = "c4e8a2d6f1b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "github_installations",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column("org_id", sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column("installation_id", sa.BigInteger(), nullable=False),
        sa.Column("account_login", sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False),
        sa.Column("account_type", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("created_by", sqlmodel.sql.sqltypes.AutoString(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("fk_github_installations_org_id_organizations")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_github_installations")),
        sa.UniqueConstraint("org_id", "installation_id", name=op.f("uq_github_installations_org_id_installation_id")),
    )
    with op.batch_alter_table("github_installations", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_github_installations_org_id"), ["org_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_github_installations_installation_id"), ["installation_id"], unique=False)

    op.create_table(
        "github_checks",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column("milestone_id", sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
        sa.Column("installation_id", sa.BigInteger(), nullable=False),
        sa.Column("repo", sqlmodel.sql.sqltypes.AutoString(length=200), nullable=False),
        sa.Column("sha", sqlmodel.sql.sqltypes.AutoString(length=40), nullable=False),
        sa.Column("check_run_id", sa.BigInteger(), nullable=True),
        sa.Column("posted_state", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=True),
        sa.Column("error", sqlmodel.sql.sqltypes.AutoString(length=300), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["milestone_id"], ["milestones.id"], name=op.f("fk_github_checks_milestone_id_milestones")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_github_checks")),
        sa.UniqueConstraint("milestone_id", "repo", "sha", name=op.f("uq_github_checks_milestone_id_repo_sha")),
    )
    with op.batch_alter_table("github_checks", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_github_checks_milestone_id"), ["milestone_id"], unique=False)

    with op.batch_alter_table("integrations", schema=None) as batch_op:
        batch_op.add_column(sa.Column("installation_id", sa.BigInteger(), nullable=True))
        batch_op.create_index(batch_op.f("ix_integrations_installation_id"), ["installation_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("integrations", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_integrations_installation_id"))
        batch_op.drop_column("installation_id")
    with op.batch_alter_table("github_checks", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_github_checks_milestone_id"))
    op.drop_table("github_checks")
    with op.batch_alter_table("github_installations", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_github_installations_installation_id"))
        batch_op.drop_index(batch_op.f("ix_github_installations_org_id"))
    op.drop_table("github_installations")
