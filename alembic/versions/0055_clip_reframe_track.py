"""Add reframe_track_jsonb to clips (expand — Issue 421 crop-track persistence)

Revision ID: 0055
Revises: 0052 (temporary — see down_revision note)
Create Date: 2026-08-04

Issue 421 (Lane L26 Track B) — the speaker-aware dynamic crop's computed
track, in the UNIFIED wire contract shape (docs/DECISIONS.md 2026-08-04,
decision 8 + Cross-Track Reconciliation §1):

    {version:1, mode, source:{width,height}, crop:{width,height}, origin_s,
     duration_s, keyframes:[{t,x}], cuts:[{t,from_x,to_x,speaker?}],
     shots:[{t}], speakers:{count,mapping_confidence}, meta:{sample_fps,fallback}}

``keyframes[].x`` is the clamped LEFT edge — the EXACT ffmpeg sendcmd values,
one geometry definition shared by render and the frontend preview overlay.
``t`` is clip-relative.

NULL = no track (flag off at render time, or the clip predates the feature) —
``GET /clips/{clip_id}/crop-track`` 404s with code ``no_crop_track``. The
track is RECOMPUTED on every render and replaced (or nulled) in the same
done-marking transaction as ``render_uri`` — the persisted row is always the
last render's track, never stale (no cache-invalidation machinery).

ON `clips`, NOT in ClipEditDocument: the edit document is user-authored state
under CAS revisions — a worker writing system output there would bump
revisions and invalidate client autosaves. Issue 396's manual override
layers ON TOP of this computed track later (two layers, two homes).

NO NEW RLS POLICY: RLS is table-scoped; `clips` already carries the
ENABLE+FORCE tenant_isolation policy and a new column inherits it.

NO INDEX: the column is only ever read via the clip's primary key.

Template A (docs/MIGRATIONS.md): nullable ADD COLUMN, no default —
metadata-only in PG16, no rewrite, lock-cheap. Expand-only: old images
ignore the column during a rolling restart.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0055"
# L26 merge: re-parent onto 0054. This branch (lane/l26-crop) forked from head
# 0052; Track A adds 0053_video_context and 0054_clip_suggested_metadata in a
# parallel branch and merges FIRST (binding cross-track contract, DECISIONS
# 2026-08-04). The merge step must flip this to "0054".
down_revision = "0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clips", sa.Column("reframe_track_jsonb", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("clips", "reframe_track_jsonb")
