"""Add peaks_uri to videos (expand — Issue 392 real waveform peaks)

Revision ID: 0051
Revises: 0050
Create Date: 2026-08-03

Issue 392 — a min/max amplitude envelope in the BBC ``audiowaveform`` JSON
format, computed at ingest from the extracted WAV and stored at
``peaks/{creator_id}/{video_id}.json``, served through an authed byte-proxy
endpoint. It replaces a FABRICATED waveform: the long-form "Source timeline"
previously drew bars at ``20 + ((i*37) % 60)%``, decoration presented to the
creator as their own audio.

NULL means "no peaks", and the UI renders an explicitly-labelled flat track
rather than inventing amplitude. Note that NULL is PERMANENT for any video whose
audio was already purged: unlike `source_uri`-derived artifacts, peaks come from
`audio_uri`, which `purge_stale_source_media` deletes at
SOURCE_MEDIA_RETENTION_HOURS (72h). The hourly backfill can only reach videos
still inside that window. This is a deliberate accepted limitation, not a bug —
see docs/COMPLIANCE.md.

ONE COLUMN ON `videos`, NOT ON `clips`. A clip is a time window into its parent
source, so the clip-level timeline reads the SAME artifact windowed to
[setup_start_s or start_s, end_s]. Adding a per-clip copy would store the same
samples N times and give #390 two code paths to render.

COLUMNS, NOT A SIDE TABLE — the artifact is 1:1 with the row and has no
independent lifecycle, exactly as argued for `poster_uri` in 0050 and
`audio_uri` in 0039.

NO NEW RLS POLICY IS REQUIRED. Postgres RLS is TABLE-scoped, not column-scoped;
`videos` already carries the ENABLE+FORCE `tenant_isolation` policy from 0010
and a new column inherits it. The 0037/0048/0049 pattern of creating a policy
applies to new TABLES only.

NO INDEX. The one new query is the hourly backfill's
`peaks_uri IS NULL AND audio_uri IS NOT NULL ... LIMIT n`, which seq-scans in
single-digit ms at current table size (signups are capped at 100 by the Google
OAuth app). RE-OPEN TRIGGER: add a partial index `WHERE peaks_uri IS NULL` via
docs/MIGRATIONS.md Template B if `videos` passes ~100k rows or the sweep shows
up in pg_stat_statements.

Template A (docs/MIGRATIONS.md): a nullable ADD COLUMN with no default is
metadata-only in PG16 — no table rewrite, no scan, lock-cheap even on populated
tables. Expand-only: old images ignore the column during a rolling restart, so
there is no contract phase.
"""

import sqlalchemy as sa

from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("videos", sa.Column("peaks_uri", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("videos", "peaks_uri")
