"""Add feedback_tags + feedback_note to clip_feedback (Issue 118)

Revision ID: 0018
Revises: 0017
Create Date: 2026-06-01
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clip_feedback", sa.Column("feedback_tags", JSONB(), nullable=True))
    op.add_column("clip_feedback", sa.Column("feedback_note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("clip_feedback", "feedback_note")
    op.drop_column("clip_feedback", "feedback_tags")
