"""Add preference_models.metrics_jsonb

Revision ID: 0042
Revises: 0041
Create Date: 2026-07-02

Issue 202: every preference-model retrain now emits a best-effort offline eval
of the new version ({"ndcg_at_5", "map_at_5", "n_eval", "computed_at"}) so a
silent ranking regression is visible per version instead of only in aggregate
harness runs. The worker compares each version's ndcg_at_5 to its predecessor
and logs a warning-severity event when the drop exceeds
PREFERENCE_NDCG_REGRESSION_THRESHOLD (warn-only ratchet — never blocks a save).

Nullable with no default: existing rows keep NULL (no eval recorded), and a
creator without enough held-out labels simply gets NULL. Downgrade drops the
column.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("preference_models", sa.Column("metrics_jsonb", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("preference_models", "metrics_jsonb")
