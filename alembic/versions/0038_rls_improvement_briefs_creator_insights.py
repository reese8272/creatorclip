"""Add tenant_isolation RLS to improvement_briefs + creator_insights

Revision ID: 0038
Revises: 0037
Create Date: 2026-06-30

Closes a tenant-isolation gap found during the L21 Issue 340b RLS sweep
(docs/OFF_COURSE_BUGS.md, 2026-06-30). Both tables carry a ``creator_id`` (per-creator
data) but were never given a row-level-security policy: ``improvement_briefs`` predates
the RLS rollout (created in 0009, before 0010) and ``creator_insights`` was added in 0017
*after* 0010 without its own policy. Newer tenant tables (e.g. ``clip_impressions`` in
0037) correctly mirror 0010, so these two were the only stragglers.

With the Issue 343 role split active (the app connects as ``creatorclip_app``, which is
NOT ``BYPASSRLS``), RLS only constrains tables that actually carry a policy — so until now
these two had no deny-by-default backstop and isolation rested entirely on application-level
``WHERE creator_id = …`` filters. This migration brings them to parity with every other
tenant table: ENABLE + FORCE row-level security and a ``tenant_isolation`` policy keyed on
the ``app.creator_id`` GUC, exactly as 0010 does.

No DDL/schema change to the tables themselves (no new columns); RLS metadata only.
"""

from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None

# The two tenant tables missed by the 0010 RLS rollout. Both have a uuid creator_id.
_TABLES = ("improvement_briefs", "creator_insights")


def upgrade() -> None:
    for table in _TABLES:
        # Re-grant DML to the app role so the policy has something to gate. 0010's
        # GRANT ON ALL TABLES + DEFAULT PRIVILEGES already cover these, but this keeps
        # the migration self-contained and idempotent.
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO creatorclip_app;")
        # FORCE makes RLS apply even to the table owner, so a non-BYPASSRLS owner
        # cannot silently sidestep the policy.
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        # Drop-then-create keeps the migration safe to re-run; CREATE POLICY has no
        # IF NOT EXISTS form in Postgres 16. The predicate matches 0010 exactly:
        # an unset GUC -> current_setting(...) returns NULL -> the row is never visible
        # (deny-by-default), and writes must carry the active creator_id.
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table};")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
                USING (creator_id = current_setting('app.creator_id', true)::uuid)
                WITH CHECK (creator_id = current_setting('app.creator_id', true)::uuid);
            """
        )


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table};")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")
        # Grants intentionally NOT revoked — they predate this migration (0010) and
        # other policies may rely on them.
