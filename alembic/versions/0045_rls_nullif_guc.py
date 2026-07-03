"""NULLIF-harden every tenant_isolation policy against the empty-string GUC

Revision ID: 0045
Revises: 0044
Create Date: 2026-07-02

Issue 354 — on a REUSED pooled connection, ``current_setting('app.creator_id',
true)`` returns ``''`` (not NULL) after a prior transaction has carried the GUC:
``set_config(..., is_local=true)`` reverts to the *empty-string placeholder* at
commit, not to "never set". The bare ``current_setting(...)::uuid`` cast then
raises ``invalid input syntax for type uuid: ""`` (SQLSTATE 22P02) → a 500 —
fails closed, but as an error instead of a clean zero-row deny.

Fix: swap the GUC expression in ALL 27 tenant_isolation policies to
``NULLIF(current_setting('app.creator_id', true), '')::uuid`` so the empty
string degrades to NULL, and ``creator_id = NULL`` denies cleanly (the same
deny-by-default path as a never-set GUC). ALTER POLICY replaces the predicate
in place; each ALTER preserves the source migration's exact USING / WITH CHECK
structure (0010/0026/0027/0029/0030/0031/0037/0038/0041 direct-column form;
0040/0044 parent-subquery form) — only the GUC expression changes.

Plain ``op.execute`` f-string SQL only — no dialect constructs (the 0041 enum
re-create incident showed those are version-fragile across environments).

Locking: ALTER POLICY takes ACCESS EXCLUSIVE on each table, but it is a
catalog-only rewrite (no table scan, no rewrite of heap data) — each lock is
held for microseconds. Alembic applies all 27 in one transaction, so locks
accumulate until commit; acceptable at beta scale (≤100 users, low write
concurrency). lock_timeout=5s / statement_timeout=120s guard both online and
offline application via alembic/env.py.
"""

from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None

# The hardened GUC expression: '' (the reused-connection placeholder) degrades
# to NULL, so the policy denies cleanly instead of raising a uuid-cast error.
_GUC_HARDENED = "NULLIF(current_setting('app.creator_id', true), '')::uuid"
# The pre-0045 bare-cast form, restored on downgrade.
_GUC_BARE = "current_setting('app.creator_id', true)::uuid"

# Tenant tables with a direct creator_id column (21). Sources: 0010 (first 12,
# alphabetical), 0026 chat_conversations, 0027 data_exports, 0029 creator_style,
# 0030 clip_publications, 0031 notifications, 0037 clip_impressions,
# 0038 improvement_briefs + creator_insights, 0041 summaries.
_DIRECT_TABLES = (
    "audience_activity",
    "chat_conversations",
    "clip_feedback",
    "clip_impressions",
    "clip_publications",
    "clips",
    "creator_dna",
    "creator_insights",
    "creator_style",
    "data_exports",
    "demographics",
    "dna_embeddings",
    "improvement_briefs",
    "minute_deductions",
    "minute_packs",
    "notifications",
    "preference_models",
    "summaries",
    "usage",
    "videos",
    "youtube_tokens",
)

# Child tables reaching tenant via a policied parent (6): (table, parent, fk).
# Sources: 0040 (first five), 0044 signals.
_CHILD_TABLES = (
    ("video_metrics", "videos", "video_id"),
    ("retention_curves", "videos", "video_id"),
    ("transcripts", "videos", "video_id"),
    ("clip_outcomes", "clips", "clip_id"),
    ("chat_messages", "chat_conversations", "conversation_id"),
    ("signals", "videos", "video_id"),
)


def _alter_all(guc_expr: str) -> None:
    for table in _DIRECT_TABLES:
        op.execute(
            f"""
            ALTER POLICY tenant_isolation ON {table}
                USING (creator_id = {guc_expr})
                WITH CHECK (creator_id = {guc_expr});
            """
        )
    for table, parent, fk in _CHILD_TABLES:
        op.execute(
            f"""
            ALTER POLICY tenant_isolation ON {table}
                USING (
                    {fk} IN (
                        SELECT id FROM {parent}
                        WHERE creator_id = {guc_expr}
                    )
                )
                WITH CHECK (
                    {fk} IN (
                        SELECT id FROM {parent}
                        WHERE creator_id = {guc_expr}
                    )
                );
            """
        )


def upgrade() -> None:
    _alter_all(_GUC_HARDENED)


def downgrade() -> None:
    _alter_all(_GUC_BARE)
