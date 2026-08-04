"""Add clip_edit_documents, and harden the 0048/0049 RLS policies

Revision ID: 0052
Revises: 0051
Create Date: 2026-08-04

Issue 391 — the creator's edit stops being browser state. Cuts lived in
``localStorage`` under ``clip:{clipId}:cuts`` with a single-slot undo and a
swallowed quota error; clearing site data, switching machines or opening a
second browser destroyed the work with no warning. This table is the
server-authoritative edit document, and the prerequisite for #393.

A NEW TABLE, NOT A JSONB COLUMN ON `clips`. 0051 argued "columns, not a side
table" for `peaks_uri` on the grounds that the artifact is 1:1 with the row and
has no independent lifecycle. That second half fails here, in four ways:
most clips are never edited; the document carries its own `revision` and
`updated_at`, which `clips` lacks entirely; the write cadence is a debounced
autosave against the hottest read table in the app, which also carries a
DEFERRABLE unique constraint that reranking permutes; and `list_clips` selects
all columns for up to 100 rows, so a JSONB doc on `clips` would be detoasted on
every library page load for a payload the list never renders.

ONE ROW PER CLIP, NOT PER-CUT ROWS. The document is always read and written
whole, so per-cut rows would turn every autosave into a diff problem that can
half-apply. One row = one UPDATE = atomic by construction. Issue 396 adds
heterogeneous content (reframe overrides, overlays, music) that the JSONB
envelope absorbs with no further migration.

`revision` IS A COLUMN, NOT A JSON KEY, because it participates in the WHERE
clause of the compare-and-set upsert that makes concurrent autosaves safe:

    INSERT ... ON CONFLICT ON CONSTRAINT uq_clip_edit_documents_clip_id
    DO UPDATE SET doc = EXCLUDED.doc, revision = clip_edit_documents.revision + 1
    WHERE clip_edit_documents.revision = :base_revision
    RETURNING revision

Per the PostgreSQL manual, a row locked but not updated because an
`ON CONFLICT DO UPDATE ... WHERE` condition was not satisfied is NOT returned —
so zero rows returned IS the stale-revision signal, with no second read.

`creator_id` IS DENORMALISED so the RLS policy is a direct-column comparison
(the house pattern) rather than a subquery through `clips`. Both FKs cascade,
which is the whole of the right-to-erasure story: `erase_creator` relies on DB
cascade and registers nothing per-table.

THIS MIGRATION ALSO REPAIRS 0048 AND 0049. Both were written after 0045 but
copied the pre-0045 template, so `video_feedback` and `creator_style_notes` are
the only two tenant tables still using the bare `::uuid` cast. On a REUSED
pooled connection `current_setting('app.creator_id', true)` returns '' rather
than NULL, and the bare cast then raises SQLSTATE 22P02 — a 500 instead of a
clean zero-row deny. Both also emit no GRANT. This is repaired here rather than
deferred because Issue 391 parameterises
`test_reused_connection_guc_less_query_denies_cleanly` to iterate every tenant
table instead of the hardcoded ("clips", "signals") pair — which is exactly why
this regression was never caught — and that test goes red on these two
otherwise. See docs/DECISIONS.md and docs/OFF_COURSE_BUGS.md.

Additive-only: a CREATE TABLE plus two ALTER POLICY statements. No backfill, no
rewrite of an existing table, so there is no contract phase. Plain `op.execute`
f-string SQL for the policy work — no dialect constructs (the 0041 enum
re-create incident showed those are version-fragile across environments).
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None

# The hardened GUC expression, identical to 0045's `_GUC_HARDENED`: '' (the
# reused-connection placeholder) degrades to NULL, so the policy denies cleanly
# instead of raising a uuid-cast error.
_GUC_HARDENED = "NULLIF(current_setting('app.creator_id', true), '')::uuid"
# The pre-0045 bare-cast form, restored on downgrade for the two repaired tables.
_GUC_BARE = "current_setting('app.creator_id', true)::uuid"

# Tables that 0048/0049 created with the pre-0045 bare cast and no GRANT.
_REPAIRED_TABLES = ("video_feedback", "creator_style_notes")


def _set_policy(table: str, guc_expr: str) -> None:
    """Point ``table``'s tenant_isolation policy at ``guc_expr``."""
    op.execute(
        f"""
        ALTER POLICY tenant_isolation ON {table}
            USING (creator_id = {guc_expr})
            WITH CHECK (creator_id = {guc_expr});
        """
    )


def upgrade() -> None:
    op.create_table(
        "clip_edit_documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "clip_id",
            sa.Uuid(),
            sa.ForeignKey("clips.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "creator_id",
            sa.Uuid(),
            sa.ForeignKey("creators.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "doc",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        # Named so the upsert can target it as `ON CONFLICT ON CONSTRAINT`, which
        # is the house idiom (billing/ledger.py increment_usage).
        sa.UniqueConstraint("clip_id", name="uq_clip_edit_documents_clip_id"),
    )
    # Every read is "this creator's document for this clip"; the unique clip_id
    # constraint already serves the lookup, so this index exists for the RLS
    # predicate and the cascade, not for a query the app issues directly.
    op.create_index(
        "ix_clip_edit_documents_creator",
        "clip_edit_documents",
        ["creator_id"],
    )

    # Belt-and-braces: 0010 set ALTER DEFAULT PRIVILEGES so new tables created by
    # the migration role are granted automatically, but 0038/0040/0043/0044 all
    # emit the explicit GRANT and 0048/0049's omission is being fixed below.
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON clip_edit_documents TO creatorclip_app;")
    op.execute("ALTER TABLE clip_edit_documents ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE clip_edit_documents FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON clip_edit_documents;")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON clip_edit_documents
            USING (creator_id = {_GUC_HARDENED})
            WITH CHECK (creator_id = {_GUC_HARDENED});
        """
    )

    # --- Repair 0048 / 0049 (see module docstring) -------------------------
    for table in _REPAIRED_TABLES:
        _set_policy(table, _GUC_HARDENED)
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO creatorclip_app;")


def downgrade() -> None:
    # Restore the bare cast on the two repaired tables so the downgrade is a
    # true inverse. Grants are deliberately not revoked, matching 0010's note
    # that roles and privileges are never torn down by a downgrade.
    for table in _REPAIRED_TABLES:
        _set_policy(table, _GUC_BARE)

    op.execute("DROP POLICY IF EXISTS tenant_isolation ON clip_edit_documents;")
    op.execute("ALTER TABLE clip_edit_documents NO FORCE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE clip_edit_documents DISABLE ROW LEVEL SECURITY;")
    op.drop_index("ix_clip_edit_documents_creator", table_name="clip_edit_documents")
    op.drop_table("clip_edit_documents")
