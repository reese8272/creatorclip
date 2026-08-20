"""Record which backend handled each notification delivery (Issue 525)

Revision ID: 0063
Revises: 0062
Create Date: 2026-08-18

`notification_deliveries.status` cannot distinguish a real delivery from a
no-op: NOTIFY_BACKEND=console makes `_send_console` log and return None, while
the row is committed status='sent' before the send. Measured on production
2026-08-18 — ENV=production, NOTIFY_BACKEND=console, 17 delivery rows, every one
'sent', not one of them delivered.

Deliberately NULLABLE with no backfill. Existing rows keep NULL, which honestly
means "written before this column existed". Re-sending them is not attempted:
they are weeks old and firing "your clips are ready" for a three-week-old video
is worse than silence (owner decision, docs/DECISIONS.md 2026-08-18).

Additive nullable column — no table rewrite, no lock of consequence.
"""

import sqlalchemy as sa

from alembic import op

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "notification_deliveries",
        sa.Column("handled_by", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("notification_deliveries", "handled_by")
