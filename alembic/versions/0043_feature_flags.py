"""Add feature_flags (runtime kill switches, Issue 284)

Revision ID: 0043
Revises: 0042
Create Date: 2026-07-02

Issue 284 — one row per flag key (llm_generation / youtube_publish /
render_intake / signup). A row overrides the env default in config.py; a
missing row falls back to it, so flipping a kill switch needs no deploy.

No RLS: a global operations table with no creator ids, no YouTube-origin data,
and no PII. Explicit GRANT to creatorclip_app mirrors 0038/0040 belt-and-
suspenders posture (0010's ALTER DEFAULT PRIVILEGES already covers new tables).
"""

import sqlalchemy as sa

from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "feature_flags",
        sa.Column("key", sa.String(length=64), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=True),
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON feature_flags TO creatorclip_app;")


def downgrade() -> None:
    op.drop_table("feature_flags")
