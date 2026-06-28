"""Add clip_impressions (per-creator impression/position log)

Revision ID: 0037
Revises: 0036
Create Date: 2026-06-27

Issue 202 — cheap insurance for counterfactual/IPS evaluation. We store each clip's
final rank but never logged what ORDER the creator actually saw, with timestamps. This
adds a per-creator impression log (clip_id, rank, shown_at) written when clips are served,
so later position-debiased (IPS) evaluation is possible — without it, that analysis can
never be done retroactively.

No PII and no YouTube-origin data: only internal ids + an integer rank + a timestamp. The
creator_id FK cascades on account deletion (right-to-erasure), and per-creator isolation is
enforced by the tenant_isolation RLS policy (mirrors 0010/0026). Index on (creator_id,
shown_at) supports the standing-report/eval reads.
"""

import sqlalchemy as sa

from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "clip_impressions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "creator_id",
            sa.Uuid(),
            sa.ForeignKey("creators.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "clip_id",
            sa.Uuid(),
            sa.ForeignKey("clips.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("shown_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_clip_impressions_creator_shown",
        "clip_impressions",
        ["creator_id", "shown_at"],
    )

    # Per-creator isolation via RLS on creator_id — mirrors 0010/0026.
    op.execute("ALTER TABLE clip_impressions ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE clip_impressions FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON clip_impressions;")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON clip_impressions
            USING (creator_id = current_setting('app.creator_id', true)::uuid)
            WITH CHECK (creator_id = current_setting('app.creator_id', true)::uuid);
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON clip_impressions;")
    op.execute("ALTER TABLE clip_impressions NO FORCE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE clip_impressions DISABLE ROW LEVEL SECURITY;")
    op.drop_index("ix_clip_impressions_creator_shown", table_name="clip_impressions")
    op.drop_table("clip_impressions")
