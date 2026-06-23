"""Add creator_style — Creator Brand Kit (Issue 186)

Revision ID: 0029
Revises: 0028
Create Date: 2026-06-23

One row per creator storing render-style defaults (brand kit). The `style`
JSONB column holds subtitle/background/captions/zoom/denoise/aspect so new
style keys can be added without a further migration.

RLS-gated on creator_id (direct column), same tenant_isolation pattern as
other per-creator tables (0010, 0025, 0026, 0027).

MERGE NOTE: this migration sets down_revision='0027'. If the parallel
`feat/batch-b-publish` branch (which added a 0027 clip_publications migration)
merges first, whichever is renumbered must update its down_revision to point
at the new head. See LEFT_OFF.md for lane-merge coordination.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "creator_style",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column(
            "creator_id",
            sa.Uuid,
            sa.ForeignKey("creators.id", ondelete="CASCADE", name="fk_creator_style_creator_id"),
            nullable=False,
        ),
        sa.Column(
            "style",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("creator_id", name="uq_creator_style_creator_id"),
    )
    op.create_index("ix_creator_style_creator_id", "creator_style", ["creator_id"])

    op.execute("ALTER TABLE creator_style ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE creator_style FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON creator_style;")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON creator_style
            USING (creator_id = current_setting('app.creator_id', true)::uuid)
            WITH CHECK (creator_id = current_setting('app.creator_id', true)::uuid);
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON creator_style;")
    op.execute("ALTER TABLE creator_style NO FORCE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE creator_style DISABLE ROW LEVEL SECURITY;")
    op.drop_index("ix_creator_style_creator_id", table_name="creator_style")
    op.drop_table("creator_style")
