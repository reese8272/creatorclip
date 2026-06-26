"""Add videos.failure_reason

Revision ID: 0036
Revises: 0035
Create Date: 2026-06-26

A failed ingest previously left the row at ``ingest_status=failed`` with no
record of WHY — the cause lived only in the worker container logs, so the
dashboard could show nothing but a bare "FAILED" badge and every diagnosis
needed a log dive. This adds a nullable, creator-safe ``failure_reason`` text
column the worker populates when it flips a video to failed (and clears on a
successful re-run). It never stores a stack trace or secret.

Nullable with no default: every existing row keeps NULL (no reason recorded),
so the upgrade is backward compatible. The downgrade simply drops the column.
"""

import sqlalchemy as sa

from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("videos", sa.Column("failure_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("videos", "failure_reason")
