"""Add pending/effective render geometry to clips (expand — Issue 470)

Revision ID: 0061
Revises: 0060
Create Date: 2026-08-13

Issue 470 — the /clean/confirm swap replaces the delivered video, but
`start_s`/`end_s`/`setup_start_s` keep the pre-trim source window, so duration,
/transcript and the caption/edit passes computed against a video that no longer
exists. Two nullable JSONB columns record what was actually delivered
(shape: {"version": 1, "keep_segments_s": [[a, b], ...], "duration_s": <sum>}):

- `pending_geometry_jsonb` — describes the artifact in `cleaned_render_uri`;
  written by the worker in the same commit (the cut list otherwise never exists
  outside the task, and the Issue-391 edit document is cleared at confirm, so
  nothing else survives to derive from). Cleared in lockstep with
  `cleaned_render_uri` by the confirm/discard CAS updates.
- `effective_geometry_jsonb` — describes the LIVE `render_uri` in original
  clip-relative time; written at confirm inside the Issue-468 CAS transaction
  (composed with the prior value for second trims); cleared by the Issue-353
  re-render reset, which regenerates the full window from source.

NO NEW RLS POLICY: RLS is table-scoped; `clips` already carries the
ENABLE+FORCE tenant_isolation policy and new columns inherit it.

NO INDEX: read on per-clip detail paths, never filtered on across the table.

Template A (docs/MIGRATIONS.md): nullable ADD COLUMN, no default —
metadata-only in PG16, no rewrite, lock-cheap. Expand-only: old images ignore
the columns during a rolling restart. Pure DDL, so no
`context.is_offline_mode()` branch is needed (contrast 0057).
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clips", sa.Column("pending_geometry_jsonb", JSONB(), nullable=True))
    op.add_column("clips", sa.Column("effective_geometry_jsonb", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("clips", "effective_geometry_jsonb")
    op.drop_column("clips", "pending_geometry_jsonb")
