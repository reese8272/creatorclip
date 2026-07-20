"""Unique backstops for the two check-then-insert races (Issue 361)

Revision ID: 0046
Revises: 0045
Create Date: 2026-07-20

Two verified SEV2 races share the same defect class — an application-level
existence check followed by an insert, with no database constraint to make the
loser fail (same class as minute_packs refunds, migration 0013):

1. ``clips`` — ``persist_ranked_clips`` (clip_engine/ranking.py) guards with
   ``load_existing_clips`` then inserts the full ranked set. Concurrent
   generation (router racing the worker pipeline, or two at-least-once Celery
   deliveries) double-inserts every clip. Backstop:
   ``uq_clips_video_rank UNIQUE (video_id, rank)``, DEFERRABLE INITIALLY
   DEFERRED because ``rerank_with_preference`` permutes rank values across an
   existing set via per-row UPDATEs — an immediate check would raise on the
   transient swap; deferred, the check runs at COMMIT and still rejects the
   racing double-insert (see docs/DECISIONS.md 2026-07-20). ``rank`` is
   NULL-able; NULLs are distinct and never conflict.

2. ``summaries`` — ``create_summary`` (routers/clips.py) probes for an
   in-flight recap then inserts + enqueues ``render_summary``; a double-click
   enqueues two ffmpeg renders. Backstop: partial unique index
   ``uq_summaries_active ON summaries (video_id) WHERE render_status IN
   ('pending', 'running')`` — done/failed rows leave the index so a later
   re-render stays possible.

Both code paths now catch ``IntegrityError`` → rollback → return the winner.

Dedupe-first: each index build is preceded by an in-migration cleanup of any
rows the un-backstopped race already produced, so the build cannot fail.
Neither cleanup is reversible (downgrade only drops the constraints).

Locking / Squawk: indexes are built ``CONCURRENTLY`` inside autocommit blocks
(cannot run in Alembic's transaction; online-safe on populated tables — same
shape as 0006/0010/0013). The clips constraint is then attached catalog-only
via ``ADD CONSTRAINT ... UNIQUE USING INDEX`` (its ACCESS EXCLUSIVE lock holds
for microseconds; no scan — timeout-guarded per alembic/env.py). Between the
dedupe commit and the concurrent build there is a small window in which the
race could re-introduce a duplicate and fail the build; a failed CONCURRENTLY
build leaves an INVALID index — drop it and re-run the migration.
"""

from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Dedupe clips double-inserted by the race: duplicates are whole re-inserted
    # sets, so the earliest-created row per (video_id, rank) is the canonical
    # one (any feedback/outcomes attach to it — the copy landed later). id is
    # uuid4 (not monotonic), so order by created_at; id only tie-breaks.
    # Deleting the later copy cascade-drops only rows the race itself created.
    op.execute(
        """
        DELETE FROM clips
        WHERE id IN (
            SELECT id FROM (
                SELECT id, ROW_NUMBER() OVER (
                    PARTITION BY video_id, rank
                    ORDER BY created_at ASC, id ASC
                ) AS rn
                FROM clips
                WHERE rank IS NOT NULL
            ) dup
            WHERE dup.rn > 1
        );
        """
    )
    # Dedupe in-flight recaps: keep the NEWEST active summary per video (the
    # row create_summary's idempotency probe returns) and demote the rest to
    # failed rather than DELETE — a worker may be mid-render against a demoted
    # id, the rows stay auditable, and 'failed' leaves the index predicate.
    op.execute(
        """
        UPDATE summaries SET render_status = 'failed'
        WHERE id IN (
            SELECT id FROM (
                SELECT id, ROW_NUMBER() OVER (
                    PARTITION BY video_id
                    ORDER BY created_at DESC, id DESC
                ) AS rn
                FROM summaries
                WHERE render_status IN ('pending', 'running')
            ) dup
            WHERE dup.rn > 1
        );
        """
    )
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_clips_video_rank "
            "ON clips (video_id, rank)"
        )
        op.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_summaries_active "
            "ON summaries (video_id) "
            "WHERE render_status IN ('pending', 'running')"
        )
    # Promote the clips index to a DEFERRABLE constraint (USING INDEX adopts —
    # and keeps the name of — the prebuilt index; catalog-only, no scan).
    # Partial indexes cannot back a constraint, so uq_summaries_active stays an
    # index; it needs no deferral (render_status transitions never permute).
    op.execute(
        "ALTER TABLE clips "
        "ADD CONSTRAINT uq_clips_video_rank UNIQUE USING INDEX uq_clips_video_rank "
        "DEFERRABLE INITIALLY DEFERRED"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE clips DROP CONSTRAINT IF EXISTS uq_clips_video_rank")
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS uq_summaries_active")
