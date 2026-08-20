"""RLS tenant_isolation on creator_identity (Issue 505)

Revision ID: 0064
Revises: 0063
Create Date: 2026-08-20

``creator_identity`` has carried a ``creator_id`` FK since Issue 83 and has never
had an RLS policy, nor a recorded exemption. It was found by the schema-derived
sweep this issue adds: nothing in the repo had ever reconciled the tenant tables
against ``pg_policies``, so a table shipping with no policy at all was invisible.

**Not an open door.** Every current query filters ``creator_id`` explicitly, so no
cross-tenant read is reachable today. This is the missing defence-in-depth layer —
the one that catches the query someone forgets to filter, which is exactly how
``improvement_briefs`` / ``creator_insights`` were caught in migration 0038.

It is also the worst of the three gaps to leave open, which is why this one gets a
policy while ``creator_api_keys`` and ``event_logs`` get recorded exemptions:
``creator_identity`` holds creator free text (``mission``, ``audience_summary``,
``style_sample``, ``hard_nos``) that is **injected into LLM prompts**, so a leak
here is a leak into another creator's generated output, not merely into a
response body.

Uses the post-0045 hardened GUC expression. The bare ``::uuid`` cast raises
SQLSTATE 22P02 on an unset GUC instead of returning zero rows, which turns
deny-by-default into a 500 — see 0045 and the clean-deny sweep in
tests/test_rls_isolation_integration.py.
"""

from alembic import op

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None

_TABLE = "creator_identity"
_GUC_HARDENED = "NULLIF(current_setting('app.creator_id', true), '')::uuid"


def upgrade() -> None:
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY;")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {_TABLE};")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON {_TABLE}
            USING (creator_id = {_GUC_HARDENED})
            WITH CHECK (creator_id = {_GUC_HARDENED});
        """
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {_TABLE};")
    op.execute(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {_TABLE} DISABLE ROW LEVEL SECURITY;")
