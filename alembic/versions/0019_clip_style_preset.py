"""Add style_preset to clips (Issue 119)

Revision ID: 0019
Revises: 0018
Create Date: 2026-06-01
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clips", sa.Column("style_preset", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("clips", "style_preset")
