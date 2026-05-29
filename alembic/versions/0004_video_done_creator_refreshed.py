"""Video.ingest_done_at + Creator.last_analytics_refreshed_at (Issues 43 + 47)

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-05-28

Bundles two nullable-column additions because they share the same migration
window and are both low-blast-radius additive changes:

  Issue 43 — videos.ingest_done_at (TIMESTAMPTZ NULL)
    Canonical "ingest pipeline finished" boundary. Replaces created_at as the
    source-media retention clock so an in-progress ingest of an old upload
    cannot have its source purged mid-pipeline. Backfill: every existing
    `done` row gets ingest_done_at = created_at. Partial index supports the
    hourly purge sweep.

  Issue 47 — creators.last_analytics_refreshed_at (TIMESTAMPTZ NULL)
    Drives the daily analytics refresh ordering. Beat job will
    `ORDER BY last_analytics_refreshed_at NULLS FIRST, id` so creators that
    starved past quota in earlier runs go first next time. No backfill —
    NULL means "never refreshed yet", which is exactly where the new sort
    wants them on day 1 (then they stamp on first success and drop to the
    back).
"""

import sqlalchemy as sa

from alembic import op

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Issue 43: videos.ingest_done_at ──────────────────────────────────────
    op.add_column(
        "videos",
        sa.Column("ingest_done_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE videos "
        "SET ingest_done_at = created_at "
        "WHERE ingest_status = 'done' AND ingest_done_at IS NULL"
    )
    op.create_index(
        "ix_videos_purge_candidates",
        "videos",
        ["ingest_done_at"],
        postgresql_where=sa.text("ingest_done_at IS NOT NULL AND source_uri IS NOT NULL"),
    )

    # ── Issue 47: creators.last_analytics_refreshed_at ───────────────────────
    op.add_column(
        "creators",
        sa.Column("last_analytics_refreshed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_creators_refresh_order",
        "creators",
        ["last_analytics_refreshed_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_creators_refresh_order", table_name="creators")
    op.drop_column("creators", "last_analytics_refreshed_at")
    op.drop_index("ix_videos_purge_candidates", table_name="videos")
    op.drop_column("videos", "ingest_done_at")
