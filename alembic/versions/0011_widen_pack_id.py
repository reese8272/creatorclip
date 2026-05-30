"""Widen minute_packs.pack_id from VARCHAR(32) to VARCHAR(64)

Revision ID: 0011_widen_pack_id
Revises: 0010_rls_policies

The original `pack_id` column was sized for Stripe Checkout session ids (`cs_…`,
~30 chars). Issue 57 introduced a refund pattern `"refund:{video_id}"` where
`video_id` is a UUID (36 chars), giving a 43-char total that overflows
VARCHAR(32) and raises `StringDataRightTruncation` at INSERT time. Caught by
the integration test `test_refund_for_video_compensates_deduction` running
under real Postgres (unit suite ran with mocks and missed it).

Widening to VARCHAR(64) leaves room for any reasonable future scheme (the
longest plausible value is still well under 64) without re-indexing — there is
no index on this column. Downgrade narrows back to VARCHAR(32); operators
must purge any over-length values first or the ALTER will fail (acceptable
because a downgrade is operator-driven).
"""

import sqlalchemy as sa

from alembic import op

revision = "0011_widen_pack_id"
down_revision = "0010_rls_policies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "minute_packs",
        "pack_id",
        existing_type=sa.String(length=32),
        type_=sa.String(length=64),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "minute_packs",
        "pack_id",
        existing_type=sa.String(length=64),
        type_=sa.String(length=32),
        existing_nullable=False,
    )
