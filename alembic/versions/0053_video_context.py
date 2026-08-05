"""Add video_context (whole-video LLM context pass) with RLS

Revision ID: 0053
Revises: 0052
Create Date: 2026-08-04

Issue 415 — the ``analyze_video_context`` chain member (between transcribe and
build_signals) stores one validated whole-video context payload per video. 1:1
child of ``videos`` (PK = FK, CASCADE) so right-to-erasure rides the existing
cascade and the PK doubles as the check-then-insert idempotency key.

RLS follows the 0044 child-table pattern verbatim (GRANT + ENABLE/FORCE RLS +
tenant_isolation policy reaching tenant via ``video_id`` → ``videos.creator_id``),
with one deliberate difference: the GUC expression is the 0045-hardened
``NULLIF(...)`` form. 0045 rewrote every existing policy to it because the bare
cast raises SQLSTATE 22P02 on a reused pooled connection; a new table must be
born hardened (the 0052 precedent) rather than re-created broken and repaired.

Plain ``op.execute`` SQL for all RLS statements — no dialect enum/type
constructs (the 0041 enum re-create incident showed those are version-fragile).
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None

_TABLE = "video_context"
_PARENT = "videos"
_FK = "video_id"

# 0045-hardened GUC: '' (the reused-connection placeholder) degrades to NULL,
# so the policy denies cleanly instead of raising a uuid-cast error.
_GUC_HARDENED = "NULLIF(current_setting('app.creator_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column(
            "video_id",
            sa.Uuid(),
            sa.ForeignKey("videos.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("context_jsonb", JSONB(), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("prompt_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

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
                    WHERE creator_id = {_GUC_HARDENED}
                )
            )
            WITH CHECK (
                {_FK} IN (
                    SELECT id FROM {_PARENT}
                    WHERE creator_id = {_GUC_HARDENED}
                )
            );
        """
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {_TABLE};")
    op.execute(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {_TABLE} DISABLE ROW LEVEL SECURITY;")
    op.drop_table(_TABLE)
