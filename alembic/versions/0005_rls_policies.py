"""Postgres Row-Level Security policies for tenant-owned tables (Issue 60)

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-05-28

Implements the RLS adopt-now decision from Issue 56. Twelve tables with a
direct ``creator_id`` column get a SELECT policy gating row visibility on
``creator_id = current_setting('app.creator_id', true)::uuid``. ``FORCE ROW
LEVEL SECURITY`` is enabled on each so the table owner cannot bypass the
policy.

Roles
=====
The application connects as ``creatorclip_app`` (no ``BYPASSRLS``); Alembic
migrations and Celery worker tasks connect as ``creatorclip_migrate``
(``BYPASSRLS``). This migration creates both roles idempotently and grants
the app role the SELECT / INSERT / UPDATE / DELETE it needs on tenant tables.
In dev / single-role setups the app role grants are no-ops because the
existing dev role is the table owner and already has full DML privileges.

The actual ``ALTER ROLE creatorclip_migrate BYPASSRLS`` and any ownership
transfer must be performed once by an operator with ``SUPERUSER`` —
documented in ``docs/DEPLOYMENT.md``. This migration does NOT attempt the
ALTER ROLE because the running migration role may not have ``SUPERUSER``;
``CREATE ROLE`` is sufficient and the BYPASSRLS attribute is granted out of
band.

Exempt tables
=============
- ``creators``: self-identifying bootstrap table — the auth dependency
  resolves the current creator by id with no policy gate, then attaches the
  resolved id to ``session.info`` so subsequent transactions inject it via
  SET LOCAL.
- ``audit_log``: shared append-only ops log — admin / oncall must see all
  rows for incident investigation.

Child tables (``video_metrics``, ``retention_curves``, ``transcripts``,
``signals``, ``clip_outcomes``) reach tenant via FK to a parent table that
has a policy. They are NOT given explicit policies in this migration —
application queries reach them via JOIN to the parent, which RLS filters.
Belt-and-suspenders policies on child tables can land in a future hardening
issue if a query path ever bypasses the parent join.
"""

from alembic import op

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


# Tables with a direct ``creator_id`` column. Ordered alphabetically.
_TENANT_TABLES = (
    "audience_activity",
    "clip_feedback",
    "clips",
    "creator_dna",
    "demographics",
    "dna_embeddings",
    "minute_deductions",
    "minute_packs",
    "preference_models",
    "usage",
    "videos",
    "youtube_tokens",
)


def upgrade() -> None:
    # ── Roles (idempotent) ───────────────────────────────────────────────────
    # CREATE ROLE … LOGIN with no password is a placeholder; the operator sets
    # a password and any extra attributes (BYPASSRLS for the migrate role) out
    # of band before the application connects with the new role name.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'creatorclip_app') THEN
                CREATE ROLE creatorclip_app LOGIN;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'creatorclip_migrate') THEN
                CREATE ROLE creatorclip_migrate LOGIN;
            END IF;
        END
        $$;
        """
    )

    # ── Grants for the app role ──────────────────────────────────────────────
    # Tenant tables: full DML (RLS gates the row visibility). Schema usage so
    # the role can reference objects. Sequence usage for any future SERIAL.
    op.execute("GRANT USAGE ON SCHEMA public TO creatorclip_app;")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO creatorclip_app;"
    )
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO creatorclip_app;")
    # Make future tables grant the app role automatically too — protects us
    # from forgetting after the next migration.
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO creatorclip_app;"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT USAGE, SELECT ON SEQUENCES TO creatorclip_app;"
    )

    # ── ENABLE + FORCE + CREATE POLICY on each tenant table ──────────────────
    for table in _TENANT_TABLES:
        # FORCE makes RLS apply even to the table owner — without it, whoever
        # owns the table would silently bypass every policy.
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        # Drop-then-create makes the migration safe to re-run after a partial
        # failure; CREATE POLICY has no IF NOT EXISTS form in Postgres 16.
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table};")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
                USING (creator_id = current_setting('app.creator_id', true)::uuid)
                WITH CHECK (creator_id = current_setting('app.creator_id', true)::uuid);
            """
        )


def downgrade() -> None:
    for table in _TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table};")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")

    # Revoke grants. Roles themselves are intentionally NOT dropped — a
    # downgrade should not destroy roles that may be in active use by other
    # processes (e.g. the app is still running on the new role). Operators
    # can DROP ROLE manually after confirming nothing else depends on them.
    op.execute(
        "REVOKE SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public FROM creatorclip_app;"
    )
    op.execute("REVOKE USAGE ON SCHEMA public FROM creatorclip_app;")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM creatorclip_app;"
    )
