"""Backfill onboarding_state=active for creators with confirmed DNA (Wave 6 Fix A)

Revision ID: 0014_backfill_onboarding_state
Revises: 0013_refund_pack_id_unique

Issue 98's `create_draft` state-machine fix (Wave 1) is forward-only: it
advances `connected → dna_pending` on every new draft, then `confirm_draft`
advances `dna_pending → active`. Creators who confirmed their DNA under
the pre-fix code path are permanently stuck — their state stayed `connected`,
the `dna_pending → active` precondition never matches, and the dashboard
"Build your Creator DNA" banner shows forever (live-observed on Backboard
Media's confirmed v2 DNA, see docs/OFF_COURSE_BUGS.md Wave-6 audit).

The right shape is a one-shot SQL backfill at the migration layer — declared,
idempotent, runs once at deploy — rather than a defensive read-time heal in
the router. The Wave 1 DECISIONS entry explicitly rejected loosening
`confirm_draft` to accept `connected` ("masks the missing transition for
every other consumer"); the same principle applies to a runtime heal in the
me-endpoint. A migration repairs the data without diluting the state machine.

`dna_pending` is INTENTIONALLY excluded from the WHERE clause — that state
legitimately represents a rebuild-in-progress (older confirmed DNA + newer
draft), where the banner copy ("Your DNA is ready — confirm your Creator
Brief") is correct. Only `connected` and `awaiting_data` are stuck states
when a confirmed DNA exists.
"""

from alembic import op

revision = "0014_backfill_onboarding_state"
down_revision = "0013_refund_pack_id_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE creators
        SET onboarding_state = 'active'
        WHERE id IN (
            SELECT DISTINCT creator_id
            FROM creator_dna
            WHERE status = 'confirmed'
        )
        AND onboarding_state IN ('connected', 'awaiting_data')
        """
    )


def downgrade() -> None:
    # State backfill is not safely reversible — we don't know what state
    # each affected creator was in before, and reverting them all to
    # `connected` would re-trigger the original Issue 98 banner-stuck bug.
    pass
