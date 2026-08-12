"""Add overlay_spans_jsonb to videos (expand — Issue 448 transient overlay bands)

Revision ID: 0058
Revises: 0057
Create Date: 2026-08-11

Issue 448 — a livestream superchat is drawn ON TOP of the camera feed, so it
lands inside the camera region Issue 439/443 resolves and the region crop
cannot exclude it. On video `7e988321` two of nine rendered clips shipped with
a stranger's donation banner burned in, one across the opening five seconds.

Shape::

    {version: 1, y, height,           # band rect, absolute SOURCE pixels
     frame: {width, height},          # source dims the band was measured against
     spans: [{start_s, end_s}, ...],  # when the band is on screen
     samples: int, sample_interval_s: float}

Deliberately NOT folded into `camera_region_jsonb`. That column holds a
component-wise median across nine windows kept only on a strict majority
(Issue 443) — its blindness to transients is the property that makes it
correct. This column records the transients themselves, so the two are
different measurements with different lifetimes and are stored separately.

Resolved during ingest, where `alocal_path` has already materialised the source
locally. NULL = not resolved (older video, detection declined, or the flag was
off) and simply means no masking, which is the pre-448 behaviour. Like the
camera region it is a cache of a deterministic measurement, never
creator-authored state, so the backfill task can recompute it at any time.

`frame` is stored so the render path can distrust a band measured against
different source dimensions rather than masking the wrong rows.

NO NEW RLS POLICY: RLS is table-scoped; `videos` already carries the
ENABLE+FORCE tenant_isolation policy and a new column inherits it.

NO INDEX: the column is only ever read via the video's primary key.

Template A (docs/MIGRATIONS.md): nullable ADD COLUMN, no default —
metadata-only in PG16, no rewrite, lock-cheap. Expand-only: old images ignore
the column during a rolling restart. No data manipulation, so no
`context.is_offline_mode()` branch is needed (contrast 0057).
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("videos", sa.Column("overlay_spans_jsonb", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("videos", "overlay_spans_jsonb")
