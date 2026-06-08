"""Add cleaned_render_uri column to clips (Issue 134 — filler + silence removal)

Revision ID: 0021
Revises: 0020
Create Date: 2026-06-07
"""

import sqlalchemy as sa

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Plain op.add_column — psycopg3 + Alembic do NOT support
    # CONCURRENTLY-equivalent column adds, and the column is nullable so the
    # add itself is fast even on the populated production table.
    op.add_column(
        "clips",
        sa.Column("cleaned_render_uri", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("clips", "cleaned_render_uri")
