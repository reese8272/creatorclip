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
    # Plain CREATE INDEX (not CONCURRENTLY) so this runs inside Alembic's
    # transaction block and works with psycopg3. The creator_insights table is
    # empty at first migration time, so no long lock. If applied to a live
    # instance with existing rows, the brief table lock is acceptable.
    op.create_index(
        "ix_creator_insight_creator_video",
        "creator_insights",
        ["creator_id", "video_id"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_creator_insight_creator_video", table_name="creator_insights")
