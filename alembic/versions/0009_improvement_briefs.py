"""improvement briefs (async 202 + poll)

Revision ID: 0009_improvement_briefs
Revises: b8c9d0e1f2a3
"""

import sqlalchemy as sa

from alembic import op

revision = "0009_improvement_briefs"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The status enum is created implicitly by create_table (the repo idiom for a
    # type's first use — see 0001's onboarding_state_enum). Do not pre-create it.
    op.create_table(
        "improvement_briefs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "creator_id",
            sa.Uuid(),
            sa.ForeignKey("creators.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("pending", "ready", "failed", name="improvement_brief_status"),
            nullable=False,
        ),
        sa.Column("brief_text", sa.Text(), nullable=True),
        sa.Column("error", sa.String(length=256), nullable=True),
        sa.Column("job_id", sa.String(length=64), nullable=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_improvement_briefs_creator_id", "improvement_briefs", ["creator_id"])


def downgrade() -> None:
    op.drop_index("ix_improvement_briefs_creator_id", table_name="improvement_briefs")
    op.drop_table("improvement_briefs")
    op.execute("DROP TYPE IF EXISTS improvement_brief_status")
