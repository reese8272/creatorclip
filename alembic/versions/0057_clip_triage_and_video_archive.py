"""Add clips.triage and videos.archived_at (expand — Issue 444 clip triage state)

Revision ID: 0057
Revises: 0056
Create Date: 2026-08-10

Issue 444 — review state existed ONLY as the append-only `clip_feedback` log,
which no read path surfaced, so the creator could not tell a reviewed clip from
an unreviewed one and the review queue reset to the first clip on every reload.

`clips.triage` is the creator's CURRENT verdict — a mutable, reversible workflow
state (`pending` → `kept` / `dropped`, and back). Deliberately NOT the same thing
as a `clip_feedback` row: feedback is an append-only training event, whereas
moving a clip between piles must be cheap and repeatable.

A native enum, like every other status column here (`render_status_enum`,
`feedback_action_enum`, `video_origin_enum`, …). The usual case for VARCHAR +
CHECK rests on `ALTER TYPE … ADD VALUE` being impossible inside a transaction —
false since PostgreSQL 12, and this project targets PG16 (CI pins
pgvector/pgvector:pg16). What stays true is that a value cannot be removed or
renamed cleanly; bounded here because the taxonomy is a fixed three piles and
the export finish line (Issue 447) gets its own column rather than more states.

NOT NULL + DEFAULT in one migration is deliberate and safe: PG11+ stores a
constant ADD COLUMN default in `pg_attribute.attmissingval`, so there is no
table rewrite and no scan. The default is also what keeps the PREVIOUS image
working through the rolling restart — its `persist_ranked_clips` INSERT does not
name `triage`, and without a server default that INSERT would violate NOT NULL
and 500 every clip generation until the restart finished. Splitting into
expand-nullable / contract-NOT-NULL would instead force `ClipOut.triage` to be
`str | None` for a whole deploy cycle for no safety gain.

`videos.archived_at` ships here although Issue 446 is what uses it, so 446 is a
pure code change and the two columns cannot disagree about what "still in the
library" means. NULL = active; a timestamp rather than a boolean because restore
and audit both want to know WHEN, and every other lifecycle marker on this schema
(`ingest_done_at`, `suggestions_generated_at`, `confirmed_at`) is already a
nullable timestamp. It is NOT an erasure mechanism — an archived row still holds
creator data, so DELETE /auth/me remains the right-to-erasure path.

NO NEW RLS POLICY: RLS is table-scoped; `clips` and `videos` already carry the
ENABLE+FORCE `tenant_isolation` policy (0010) and new columns inherit it. The
backfill below runs as the migration role, which has BYPASSRLS — verify on
staging, because a policy-filtered backfill fails SILENTLY (0 rows, exit 0).

NO INDEX: every triage read is anchored by an equality on `video_id`
(`ix_clips_video_id`, migration 0001) over at most CLIP_REGEN_TOTAL_CAP=24
engine clips, or aggregates all of a creator's clips in a GROUP BY that must
read every row regardless. A 3-value enum index would never be chosen by the
planner — `ix_clips_render_status` (0001) is that same mistake already in the
schema; don't repeat it.

Templates A + Rule 4 (docs/MIGRATIONS.md). NOTE: Rule 4's snippet uses
`UPDATE … LIMIT`, which is not valid PostgreSQL (UPDATE has no LIMIT clause);
this migration is the first batched backfill in the repo, so the rule had never
been exercised. Logged in docs/OFF_COURSE_BUGS.md. The correct form is the CTE
below. Alembic wraps the whole migration in ONE transaction (alembic/env.py), so
batching bounds each statement's row count and lock set — keeping every statement
under the connect-time `statement_timeout` — but does not split the WAL burst.
At v1 scope (≤100-user beta) that is correct and simplest; past ~1M clips this
loop belongs in a one-shot Celery task under an advisory lock instead.
"""

import sqlalchemy as sa
from sqlalchemy import text

from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None

_ENUM_NAME = "clip_triage_enum"
_ENUM_VALUES = ("pending", "kept", "dropped")
_BACKFILL_BATCH = 1000

# Derive the starting verdict from the creator's LATEST feedback per clip — the
# same newest-wins rule preference/train.py now trains on.
#
# `format` counts as a keep HERE (the creator bothered to render it, which is
# evidence they wanted it) even though preference/train.py excludes it from the
# verdict partition (choosing an aspect ratio is not a judgement of quality).
# Different questions, deliberately different action sets — do not "fix" one to
# match the other. `skip` is in neither: skipping is not a verdict, so those
# clips stay `pending` and correctly reappear in the queue.
_BACKFILL_SQL = text(
    """
    WITH latest AS (
        SELECT DISTINCT ON (f.clip_id) f.clip_id, f.action
        FROM clip_feedback f
        ORDER BY f.clip_id, f.created_at DESC, f.id DESC
    ),
    batch AS (
        SELECT l.clip_id,
               CASE WHEN l.action = 'downvote' THEN 'dropped' ELSE 'kept' END AS state
        FROM latest l
        JOIN clips c ON c.id = l.clip_id
        WHERE c.triage = 'pending'
          AND l.action IN ('upvote', 'trim', 'format', 'downvote')
        LIMIT :batch
    )
    UPDATE clips c
    SET triage = b.state::clip_triage_enum
    FROM batch b
    WHERE c.id = b.clip_id
    """
)


def upgrade() -> None:
    triage = sa.Enum(*_ENUM_VALUES, name=_ENUM_NAME)
    triage.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "clips",
        sa.Column("triage", triage, nullable=False, server_default="pending"),
    )
    op.add_column("videos", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))

    # Bounded batches. Terminates because every iteration moves rows off
    # 'pending', shrinking the candidate set; clips whose latest action is
    # `skip` are excluded by the action filter and so never stall the loop.
    connection = op.get_bind()
    while True:
        result = connection.execute(_BACKFILL_SQL, {"batch": _BACKFILL_BATCH})
        if result.rowcount == 0:
            break


def downgrade() -> None:
    op.drop_column("videos", "archived_at")
    # drop_column BEFORE DROP TYPE, or Postgres refuses: "cannot drop type …
    # because other objects depend on it".
    op.drop_column("clips", "triage")
    sa.Enum(*_ENUM_VALUES, name=_ENUM_NAME).drop(op.get_bind(), checkfirst=True)
