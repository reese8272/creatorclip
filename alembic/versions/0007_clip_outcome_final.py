"""clip_outcomes.final terminal marker (Issue 70)

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-05-29

Issue 70 — poll_clip_outcomes re-qualified every published clip for a
YouTube-quota-costing re-poll every 7 days forever (the `fetched_at < cutoff_7d`
branch had no terminal guard). `final` marks an outcome as fully measured (its 7d
checkpoint recorded) so it is never polled again.

Backfill: existing rows default to FALSE and finalize on their next 7d poll, then
stop — self-draining, no data migration needed. A partial index supports the
hourly sweep's "not yet final" filter cheaply.
"""

import sqlalchemy as sa

from alembic import op

revision = "a7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "clip_outcomes",
        sa.Column("final", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index(
        "ix_clip_outcomes_poll_candidates",
        "clip_outcomes",
        ["fetched_at"],
        postgresql_where=sa.text("final = false AND published_youtube_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_clip_outcomes_poll_candidates", table_name="clip_outcomes")
    op.drop_column("clip_outcomes", "final")
