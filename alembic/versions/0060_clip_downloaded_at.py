"""Add downloaded_at to clips (expand — Issue 447 Keep-pile finish line)

Revision ID: 0060
Revises: 0059
Create Date: 2026-08-13

Issue 447 — a download is a 302 to a presigned URL with no server-side record,
so "did I already download this?" was unanswerable and the Keep pile could not
show a rendered → downloaded → published finish line. A COLUMN rather than an
inference (owner-approved ruling): `ClipPublication` records publishes, not
downloads, and `event_logs` is purged on a 90-day cycle — neither can answer
the question durably.

Semantics: set when a download of the CURRENT render is initiated (the 302 /
file response issued with disposition=attachment; the same endpoint backs the
in-app player with disposition=inline, which never stamps). Cleared on
re-render (the Issue-353 reset) and on /clean/confirm's artifact swap — both
replace the render the stamp described.

NO NEW RLS POLICY: RLS is table-scoped; `clips` already carries the
ENABLE+FORCE tenant_isolation policy and a new column inherits it.

NO INDEX: the column is read on per-video clip lists (bounded at 100 rows) and
never filtered on across the table.

Template A (docs/MIGRATIONS.md): nullable ADD COLUMN, no default —
metadata-only in PG16, no rewrite, lock-cheap. Expand-only: old images ignore
the column during a rolling restart. Pure DDL, so no
`context.is_offline_mode()` branch is needed (contrast 0057).
"""

import sqlalchemy as sa

from alembic import op

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clips", sa.Column("downloaded_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("clips", "downloaded_at")
