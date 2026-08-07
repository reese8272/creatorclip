"""Add camera_region_jsonb to videos (expand — Issue 439 video-level camera region)

Revision ID: 0056
Revises: 0055
Create Date: 2026-08-07

Issue 439 — the camera region resolved ONCE per video instead of independently
per clip. Shape::

    {version: 1, x, y, width, height,
     frame: {width, height},      # source dims the rect was measured against
     sample_frames: int}          # how many frames the consensus was drawn from

Computed during ingest, where `alocal_path` has already materialised the source
locally (a separate task would pay a second full download of a
several-hundred-MB file). The render path prefers this rect and falls back to
per-clip detection when it is absent — so videos ingested before this migration
keep working unchanged.

NULL = not resolved (older video, detection declined, or the flag was off at
ingest). It is a cache of a deterministic measurement, never creator-authored
state, so it can be recomputed at any time by the backfill task.

`frame` is stored so the render path can distrust a rect measured against
different source dimensions rather than cropping to a stale geometry.

NO NEW RLS POLICY: RLS is table-scoped; `videos` already carries the
ENABLE+FORCE tenant_isolation policy and a new column inherits it.

NO INDEX: the column is only ever read via the video's primary key.

Template A (docs/MIGRATIONS.md): nullable ADD COLUMN, no default —
metadata-only in PG16, no rewrite, lock-cheap. Expand-only: old images ignore
the column during a rolling restart.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("videos", sa.Column("camera_region_jsonb", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("videos", "camera_region_jsonb")
