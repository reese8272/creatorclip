"""Add stripe_customer_id to creators

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-05-26

"""

import sqlalchemy as sa

from alembic import op

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "creators",
        sa.Column("stripe_customer_id", sa.String(256), nullable=True),
    )
    op.create_index(
        "ix_creators_stripe_customer_id",
        "creators",
        ["stripe_customer_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_creators_stripe_customer_id", table_name="creators")
    op.drop_column("creators", "stripe_customer_id")
