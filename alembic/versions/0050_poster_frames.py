"""Add poster_uri to videos + clips (expand — Issue 387 poster frames)

Revision ID: 0050
Revises: 0049
Create Date: 2026-08-03

Issue 387 — a poster frame extracted at ingest (videos) and at render (clips),
stored at posters/{creator_id}/... and served through the authed byte-proxy
endpoints. NULL means "no poster": either not yet extracted, or the source was
purged before the backfill reached it — the UI renders a placeholder rather
than a broken image.

COLUMNS, NOT A SIDE TABLE. The artifact is 1:1 with the row and has no
independent lifecycle, exactly like videos.audio_uri (0039) and
clips.render_uri. list_videos and _clip_response already hold the parent row,
so a side table would only add a join to the hottest list endpoints for what is
effectively a boolean. A separate table would earn its keep only for N posters
per video — a hover-scrub sprite strip — which is explicitly scoped out of #387
and would be a different artifact with its own key, size and retention anyway.

NO NEW RLS POLICY IS REQUIRED. Postgres RLS is TABLE-scoped, not column-scoped;
`videos` and `clips` both already carry the ENABLE+FORCE `tenant_isolation`
policy from 0010, and a new column inherits it. The 0037/0048/0049 pattern of
creating a policy applies to new TABLES only. Stated explicitly so the next
reviewer does not have to re-derive it.

NO INDEX. The one new query is the hourly backfill's
`poster_uri IS NULL AND source_uri IS NOT NULL ... LIMIT n`, which seq-scans in
single-digit ms at current table size (signups are capped at 100 by the Google
OAuth app). RE-OPEN TRIGGER: add a partial index `WHERE poster_uri IS NULL` via
docs/MIGRATIONS.md Template B (CREATE INDEX CONCURRENTLY inside an
autocommit_block) if `videos` passes ~100k rows or the sweep shows up in
pg_stat_statements.

Template A (docs/MIGRATIONS.md): a nullable ADD COLUMN with no default is
metadata-only in PG16 — no table rewrite, no scan, lock-cheap even on populated
tables. Expand-only: old images ignore the column during a rolling restart, so
there is no contract phase.
"""

import sqlalchemy as sa

from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("videos", sa.Column("poster_uri", sa.Text(), nullable=True))
    op.add_column("clips", sa.Column("poster_uri", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("clips", "poster_uri")
    op.drop_column("videos", "poster_uri")
