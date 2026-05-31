"""Partial UNIQUE on minute_packs.pack_id WHERE reason = 'refund' (Wave-4 Fix 2)

Revision ID: 0013_refund_pack_id_unique
Revises: 0012_creator_identity

Issue 57's refund flow uses ``pack_id = "refund:<video_id>"`` to key the
idempotency of automatic refunds. The application-layer guard at
``billing/refund.py:50-56`` was a read-then-write (SELECT id WHERE pack_id =
<refund_key>, then INSERT if None) — Celery's ``task_acks_late=True`` plus
worker preemption can deliver two concurrent ``on_failure`` callbacks for the
same video. Both pass the SELECT, both INSERT → the creator is double-credited.

The DB-level guarantee — a partial UNIQUE index — is the correct shape. ``WHERE
reason = 'refund'`` is critical because the column is reused by purchase rows
(``"trial"``, ``"starter"``, ``"creator"``, ``"pro"``, ``"studio"``,
``"regular"``) which intentionally share ``pack_id`` values across creators; a
global UNIQUE on ``pack_id`` would break those.

Pattern matches:
- ``creator_dna.build_job_id`` partial UNIQUE (Issue 76 / migration 0008)
- ``creator_identity`` partial UNIQUE on "current row" (migration 0012)
- ``MinuteDeduction.UNIQUE(video_id)`` (Issue 34)

Built CONCURRENTLY inside an autocommit block — required for
``CREATE INDEX CONCURRENTLY`` (cannot run in a transaction) and keeps the build
online-safe on the populated table. Same shape as migrations 0006 and 0010.

After this index exists, ``billing/refund.py`` drops its read-then-write guard
and relies on ``grant_minutes``'s existing ``IntegrityError`` catch path to
no-op a concurrent duplicate.
"""

from alembic import op

revision = "0013_refund_pack_id_unique"
down_revision = "0012_creator_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "
            "uq_minute_packs_refund_pack_id "
            "ON minute_packs (pack_id) "
            "WHERE reason = 'refund'"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS uq_minute_packs_refund_pack_id")
