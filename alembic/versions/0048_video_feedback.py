"""Add video_feedback (video-level style review)

Revision ID: 0048
Revises: 0047
Create Date: 2026-07-30

Issue 370 — the standalone Review tool records what a creator likes/dislikes
about a whole video's STYLE (valence + tags + free-text note), mirroring the
ClipFeedback taxonomy shape so the style distiller (Issue 371) consumes
uniform triples from both tables.

Creator-authored content only — no PII beyond what the creator types, no
YouTube-origin data. creator_id FK cascades on account deletion
(right-to-erasure); per-creator isolation via the tenant_isolation RLS policy
(mirrors 0010/0037). Index on (creator_id, created_at) supports the
distillation watermark scan; (video_id) supports the per-video GET (Postgres
does not auto-index FKs).
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_feedback",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "creator_id",
            sa.Uuid(),
            sa.ForeignKey("creators.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "video_id",
            sa.Uuid(),
            sa.ForeignKey("videos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "sentiment",
            sa.Enum("like", "dislike", name="video_sentiment_enum"),
            nullable=False,
        ),
        sa.Column("feedback_tags", JSONB(), nullable=True),
        sa.Column("feedback_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_video_feedback_creator_created",
        "video_feedback",
        ["creator_id", "created_at"],
    )
    op.create_index("ix_video_feedback_video", "video_feedback", ["video_id"])

    # Per-creator isolation via RLS on creator_id — mirrors 0010/0037.
    op.execute("ALTER TABLE video_feedback ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE video_feedback FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON video_feedback;")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON video_feedback
            USING (creator_id = current_setting('app.creator_id', true)::uuid)
            WITH CHECK (creator_id = current_setting('app.creator_id', true)::uuid);
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON video_feedback;")
    op.execute("ALTER TABLE video_feedback NO FORCE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE video_feedback DISABLE ROW LEVEL SECURITY;")
    op.drop_index("ix_video_feedback_video", table_name="video_feedback")
    op.drop_index("ix_video_feedback_creator_created", table_name="video_feedback")
    op.drop_table("video_feedback")
    op.execute("DROP TYPE IF EXISTS video_sentiment_enum;")
