"""Add clips.suggested_* metadata columns (batched auto-metadata)

Revision ID: 0054
Revises: 0053
Create Date: 2026-08-04

Issue 417 — the pipeline's batched metadata task writes suggested
title/description/hook for every ranked clip (ONE structured-output call per
video), pre-clamped to the YouTube videos.insert limits. ``applied_*`` (0047)
remains creator-typed only; publish falls back applied_* → suggested_* →
(video.title | "#Shorts"). ``suggestions_generated_at`` stamps the batch write;
``suggested_title IS NULL`` is the redelivery fill-only idempotency filter.

Plain nullable TEXT columns on the existing RLS-policied ``clips`` table — no
new policy work needed (tenant_isolation on clips already covers them).
"""

import sqlalchemy as sa

from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clips", sa.Column("suggested_title", sa.Text(), nullable=True))
    op.add_column("clips", sa.Column("suggested_description", sa.Text(), nullable=True))
    op.add_column("clips", sa.Column("suggested_hook", sa.Text(), nullable=True))
    op.add_column(
        "clips",
        sa.Column("suggestions_generated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("clips", "suggestions_generated_at")
    op.drop_column("clips", "suggested_hook")
    op.drop_column("clips", "suggested_description")
    op.drop_column("clips", "suggested_title")
