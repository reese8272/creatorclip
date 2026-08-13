"""Delete feedback `skip` rows that mask a kept/dropped clip's real label (data — Issue 472)

Revision ID: 0062
Revises: 0061
Create Date: 2026-08-13

Issue 472 (SEV1) — the shipped Trim → Skip review flow POSTed a `skip` feedback
row after the keep label. `skip` is part of the verdict partition
(preference/train.py), so that row won the per-clip partition and dropped out at
the trainable filter: the keep label was silently retracted while `clips.triage`
stayed `kept`. The code fix makes POST /feedback acknowledge `skip` without
persisting it; this DATA migration repairs the rows the old code already wrote.

Target set — exactly the masking rows, nothing else: `skip` rows on clips whose
triage is `kept`/`dropped` with NO later trainable verdict (`upvote`/`trim`/
`downvote`) on the same clip. A trailing run of several skips is removed whole
(the NOT EXISTS is against trainable rows, so every skip in the tail qualifies —
the closed form of "repeat: delete the latest-verdict skip until none is left").
Deliberately preserved:

- skips SUPERSEDED by a later trainable verdict — masked already, historical;
- skips on `pending` clips — those are PUT /triage retractions doing their job
  (the creator sent the clip back to the queue; the label must stay withdrawn).

Idempotent: a second run matches zero rows. The would-delete count is logged
BEFORE the delete so the prod apply leaves an audit trail of the repair size.

Batched per docs/MIGRATIONS.md Rule 4 (Template: 0057, the first batched data
migration): bounded DELETE loop online; the OFFLINE branch
(`context.is_offline_mode()` — `alembic upgrade --sql`, which the CI
migration-lint job renders) has no connection, so `execute()` returns None and
a row-count loop would crash on `.rowcount`; it emits the equivalent single
unbounded statement instead.

Runs as the migration role (BYPASSRLS) — the repair is deliberately
cross-tenant: every creator the old UI flow touched.
"""

import logging

from sqlalchemy import text

from alembic import context, op

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None

# NOT the "alembic.*" namespace: the alembic package installs a NullHandler on
# its own logger, which swallows records before logging.lastResort can print
# them (env.py never calls fileConfig, so no real handler exists anywhere).
logger = logging.getLogger(__name__)

_BATCH = 1000

# A skip row "masks" a label iff its clip is kept/dropped and no trainable
# verdict was recorded after it. (created_at, id) ordering matches
# latest_verdict_subquery's tie-break exactly.
_MASKING_SKIP_PREDICATE = """
    f.action = 'skip'
    AND EXISTS (
        SELECT 1 FROM clips c
        WHERE c.id = f.clip_id AND c.triage IN ('kept', 'dropped')
    )
    AND NOT EXISTS (
        SELECT 1 FROM clip_feedback g
        WHERE g.clip_id = f.clip_id
          AND g.action IN ('upvote', 'trim', 'downvote')
          AND (g.created_at, g.id) > (f.created_at, f.id)
    )
"""

_COUNT_SQL = text(
    f"SELECT COUNT(*) FROM clip_feedback f WHERE {_MASKING_SKIP_PREDICATE}"  # noqa: S608
)

_DELETE_BATCH_SQL = text(
    f"""
    DELETE FROM clip_feedback
    WHERE id IN (
        SELECT f.id FROM clip_feedback f
        WHERE {_MASKING_SKIP_PREDICATE}
        LIMIT :batch
    )
    """  # noqa: S608
)

# Offline (`--sql`) rendering cannot loop on rowcounts — emit the unbounded
# equivalent for a human applying the script by hand (same shape as 0057).
_DELETE_ALL_SQL = f"""
    DELETE FROM clip_feedback
    WHERE id IN (
        SELECT f.id FROM clip_feedback f
        WHERE {_MASKING_SKIP_PREDICATE}
    )
"""  # noqa: S608


def upgrade() -> None:
    if context.is_offline_mode():
        op.execute(_DELETE_ALL_SQL)
        return

    connection = op.get_bind()
    would_delete = connection.execute(_COUNT_SQL).scalar_one()
    # WARNING, not INFO: alembic/env.py never calls fileConfig and the CLI
    # process configures no handlers, so INFO records are dropped silently.
    # WARNING reaches logging.lastResort (stderr) — the deploy log keeps an
    # audit line for how many rows this destructive repair removed.
    logger.warning("Issue 472 repair: deleting %d masking skip feedback row(s)", would_delete)

    # Terminates: every iteration removes rows from the (fixed) masking set.
    # Deleting a tail skip can promote the skip before it into the "no later
    # trainable verdict" condition, but that earlier skip already satisfied the
    # predicate (the condition ignores other skips), so the set never grows.
    while True:
        result = connection.execute(_DELETE_BATCH_SQL, {"batch": _BATCH})
        if result.rowcount == 0:
            break


def downgrade() -> None:
    # Data repair — the deleted rows were pure defects with no information the
    # product needs back; recreating them would re-erase the creators' labels.
    pass
