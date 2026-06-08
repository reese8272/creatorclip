"""Add Creator.trial_ends_at (Issue 126)

Revision ID: 0023
Revises: 0022
Create Date: 2026-06-08

Nullable timezone-aware datetime; legacy rows keep NULL. routers/auth.py
sets this on first OAuth login (now + TRIAL_DURATION_DAYS); the 402 paywall
in billing/ledger.py reads it live to differentiate "trial ended" vs
"balance exhausted" copy.
"""

import sqlalchemy as sa

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "creators",
        sa.Column(
            "trial_ends_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("creators", "trial_ends_at")
