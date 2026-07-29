"""Add applied_title / applied_description to clips (expand — Wave-1 metadata)

Revision ID: 0047
Revises: 0046
Create Date: 2026-07-29

Creator-approved publish metadata (Issue 322's title suggestions previously
never reached YouTube — the publish task hardcoded video.title + "#Shorts").
NULL means "no applied value": the worker falls back to video.title / "#Shorts"
at publish time.

Template A (docs/MIGRATIONS.md): nullable ADD COLUMN with no default is
metadata-only in PG16 — no table rewrite, no scan, lock-cheap on the populated
clips table. Old images simply ignore the columns during the rolling-restart
window (expand-only; no contract phase needed).
"""

import sqlalchemy as sa

from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "clips",
        sa.Column("applied_title", sa.Text(), nullable=True),
    )
    op.add_column(
        "clips",
        sa.Column("applied_description", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("clips", "applied_description")
    op.drop_column("clips", "applied_title")
