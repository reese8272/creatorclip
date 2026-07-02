"""Add RLS to the signals table (the child table 0040 omitted)

Revision ID: 0044
Revises: 0043
Create Date: 2026-07-02

Issue 231 — worker tenant tasks now run on the RLS-gated app role via
``db.tenant_session``, so every child table the pipeline reads or writes must
carry its own tenant_isolation policy. Migration 0040 policed video_metrics /
retention_curves / transcripts / clip_outcomes / chat_messages but omitted
``signals`` (per-video signal timelines written by ``build_signals``). This
migration closes that gap with the exact 0040 subquery pattern: signals reach
tenant via ``video_id`` → ``videos.creator_id``.

Plain ``op.execute`` SQL only — no dialect enum/type constructs (the 0041 enum
re-create incident showed those are version-fragile across environments).
"""

from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None

_TABLE = "signals"
_PARENT = "videos"
_FK = "video_id"


def upgrade() -> None:
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_TABLE} TO creatorclip_app;")
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY;")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {_TABLE};")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON {_TABLE}
            USING (
                {_FK} IN (
                    SELECT id FROM {_PARENT}
                    WHERE creator_id = current_setting('app.creator_id', true)::uuid
                )
            )
            WITH CHECK (
                {_FK} IN (
                    SELECT id FROM {_PARENT}
                    WHERE creator_id = current_setting('app.creator_id', true)::uuid
                )
            );
        """
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {_TABLE};")
    op.execute(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {_TABLE} DISABLE ROW LEVEL SECURITY;")
