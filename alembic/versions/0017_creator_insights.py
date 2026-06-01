"""creator_insights table (Issue 117)

Revision ID: 0017
Revises: 0016
Create Date: 2026-06-01
"""

import sqlalchemy as sa

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "creator_insights",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "creator_id",
            sa.Uuid(),
            sa.ForeignKey("creators.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "video_id",
            sa.Uuid(),
            sa.ForeignKey("videos.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "insight_type",
            sa.Enum("performer_analysis", "trend", "recommendation", name="insight_type_enum"),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("dna_version", sa.Integer(), nullable=True),
        sa.Column("is_saved", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("creator_insights")
    op.execute("DROP TYPE IF EXISTS insight_type_enum")
