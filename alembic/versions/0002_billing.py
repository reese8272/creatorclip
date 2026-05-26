"""Billing: replace plan_tier/subscription_status with minutes_balance/stripe_customer_id; add minute_packs table

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
    # ── creators table ────────────────────────────────────────────────────────
    op.drop_column("creators", "plan_tier")
    op.drop_column("creators", "subscription_status")
    op.add_column(
        "creators",
        sa.Column("stripe_customer_id", sa.String(64), nullable=True),
    )
    op.add_column(
        "creators",
        sa.Column(
            "minutes_balance",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
    )

    # ── minute_packs table ────────────────────────────────────────────────────
    op.create_table(
        "minute_packs",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column(
            "creator_id",
            sa.Uuid,
            sa.ForeignKey("creators.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("pack_id", sa.String(32), nullable=False),
        sa.Column("minutes_granted", sa.Integer, nullable=False),
        sa.Column("price_cents", sa.Integer, nullable=False, server_default="0"),
        sa.Column("stripe_session_id", sa.String(128), nullable=True, unique=True),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_minute_packs_creator_id", "minute_packs", ["creator_id"])


def downgrade() -> None:
    op.drop_table("minute_packs")
    op.drop_column("creators", "minutes_balance")
    op.drop_column("creators", "stripe_customer_id")
    op.add_column("creators", sa.Column("plan_tier", sa.String(64), nullable=True))
    op.add_column("creators", sa.Column("subscription_status", sa.String(64), nullable=True))
