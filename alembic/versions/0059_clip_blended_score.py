"""Add blended_score to clips (expand — Issue 465 score-semantics separation)

Revision ID: 0059
Revises: 0058
Create Date: 2026-08-12

Issue 465 — `rerank_with_preference` wrote the personalization blend back into
`clips.score`, so once a creator crossed the threshold the persisted value
silently stopped being the DNA/LLM fit composite that every downstream reader
(recap candidates, proof-of-lift, chat tools, the efficacy harness's
`dna_composite`) treats it as. This column separates the two:

- `score`          — immutable DNA/LLM fit composite, set once at persist time
- `blended_score`  — (1-w)*score + w*pref, written by the preference rerank;
                     NULL = personalization never applied (below threshold, no
                     model, or the Issue-431 append path which skips the rerank)

NO BACKFILL, deliberately: for pre-0059 rows that were reranked, the overwrite
already destroyed the original fit — the blend cannot be un-mixed, so there is
nothing correct to copy in either direction. Those rows may carry a historical
blend in `score`; the contamination is bounded ((1-w) >= 1 - PREFERENCE_WEIGHT_CAP
of the value is fit) and washes out as clips regenerate.

NO NEW RLS POLICY: RLS is table-scoped; `clips` already carries the
ENABLE+FORCE tenant_isolation policy and a new column inherits it.

NO INDEX: ordering by blended score happens in-process over one video's clip
list, never as a table scan.

Template A (docs/MIGRATIONS.md): nullable ADD COLUMN, no default —
metadata-only in PG16, no rewrite, lock-cheap. Expand-only: old images ignore
the column during a rolling restart. Pure DDL, so no
`context.is_offline_mode()` branch is needed (contrast 0057).
"""

import sqlalchemy as sa

from alembic import op

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clips", sa.Column("blended_score", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("clips", "blended_score")
