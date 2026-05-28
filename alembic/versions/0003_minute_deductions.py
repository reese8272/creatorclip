"""Per-video idempotent minute deductions ledger (Issue 34)

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-05-28

Adds the `minute_deductions` table — symmetric to `minute_packs` (grants ledger).
UNIQUE(video_id) is the idempotency key: prevents Celery at-least-once redelivery
from double-deducting minutes when an ingest task re-runs.
"""

import sqlalchemy as sa

from alembic import op

revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "minute_deductions",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column(
            "video_id",
            sa.Uuid,
            sa.ForeignKey("videos.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "creator_id",
            sa.Uuid,
            sa.ForeignKey("creators.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("minutes_deducted", sa.Integer, nullable=False),
        sa.Column("duration_s", sa.Float, nullable=False),
        sa.Column(
            "deducted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_minute_deductions_creator_deducted_at",
        "minute_deductions",
        ["creator_id", "deducted_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_minute_deductions_creator_deducted_at", table_name="minute_deductions")
    op.drop_table("minute_deductions")
