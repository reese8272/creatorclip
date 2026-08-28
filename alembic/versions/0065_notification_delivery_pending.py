"""Add 'pending' to notification_delivery_status_enum (Issue 530)

Revision ID: 0065
Revises: 0064
Create Date: 2026-08-28

The send path committed every delivery row status='sent' BEFORE the provider
call (the Issue-349 connection-freeing commit), so a worker killed mid-send
left a permanently latched false 'sent' that the retry guard could never
adopt. The fix writes the row 'pending' and flips it to 'sent' only after the
mailer returns; 'pending' is therefore the one non-terminal state and needs to
exist in the native enum.

ALTER TYPE ... ADD VALUE appends at the end, which is fine: enum order carries
no meaning for this column and appending keeps the migration-lint round-trip
byte-identical (downgrade recreates the type without 'pending'; re-upgrade
appends it back to the same position). Since PostgreSQL 12 ADD VALUE is legal
inside a transaction as long as the new value is not used in the same
transaction — and this migration never writes a row.

The downgrade is REAL (no DOWNGRADE_EXCEPTIONS entry): 'pending' rows collapse
to 'failed' — the closest honest meaning for "claimed but never confirmed
sent", and the one status the pre-530 retry guard can still adopt — then the
type is recreated without the value. The recreate takes ACCESS EXCLUSIVE and
rewrites the column, acceptable because notification_deliveries is tiny
(17 rows on prod as of 2026-08-18) and downgrade is break-glass only.
"""

from alembic import op

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None

_ENUM = "notification_delivery_status_enum"


def upgrade() -> None:
    op.execute(f"ALTER TYPE {_ENUM} ADD VALUE IF NOT EXISTS 'pending'")


def downgrade() -> None:
    # Collapse the value being removed first, or the cast below fails.
    op.execute("UPDATE notification_deliveries SET status = 'failed' WHERE status = 'pending'")
    op.execute(f"ALTER TYPE {_ENUM} RENAME TO {_ENUM}_old")
    op.execute(f"CREATE TYPE {_ENUM} AS ENUM ('sent', 'skipped', 'failed')")
    op.execute(
        f"ALTER TABLE notification_deliveries "
        f"ALTER COLUMN status TYPE {_ENUM} USING status::text::{_ENUM}"
    )
    op.execute(f"DROP TYPE {_ENUM}_old")
