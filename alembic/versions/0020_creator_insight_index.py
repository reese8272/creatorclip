"""Add composite index on creator_insights(creator_id, video_id) (Issue 123)

Revision ID: 0020
Revises: 0019
Create Date: 2026-06-07
"""

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # CREATE INDEX CONCURRENTLY must run outside a transaction.
    op.execute("COMMIT")
    op.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_creator_insight_creator_video "
        "ON creator_insights (creator_id, video_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_creator_insight_creator_video")
