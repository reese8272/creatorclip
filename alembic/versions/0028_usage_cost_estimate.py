"""Add cost_estimate column to usage table (Issue 220)

Revision ID: 0028
Revises: 0027
Create Date: 2026-06-23

Adds a Numeric(12,6) cost_estimate column to the usage table, persisting the
estimated USD cost at write time so billing/metrics can read USD without a
price-book join at query time.

NB: other lanes running in parallel may also add a 0028 migration against the
same 0027 head. Whichever merges second must be renumbered (e.g. 0029) with
its down_revision updated accordingly — see docs/BRANCHING.md and LEFT_OFF.md.
"""

import sqlalchemy as sa

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "usage",
        sa.Column("cost_estimate", sa.Numeric(precision=12, scale=6), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("usage", "cost_estimate")
