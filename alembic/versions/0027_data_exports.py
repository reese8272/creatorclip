"""Add data_exports — async GDPR Art. 15/20 export jobs (Issue 249)

Revision ID: 0027
Revises: 0026
Create Date: 2026-06-22

One tenant row per creator tracking an async data-export job (status +
artifact URI), polled by GET /creators/me/export. RLS-gated on creator_id
(direct column), same tenant_isolation pattern as 0010 / 0026.

NB: the parallel `feat/batch-b-publish` branch also adds a 0027 migration
(clip_publications). Whichever merges second must be renumbered to 0028 with
its down_revision pointing at this one — see LEFT_OFF.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "data_exports",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "creator_id",
            UUID(as_uuid=True),
            sa.ForeignKey("creators.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("pending", "ready", "failed", name="data_export_status_enum"),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("export_uri", sa.Text, nullable=True),
        sa.Column("error", sa.String(256), nullable=True),
        sa.Column("job_id", sa.String(64), nullable=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("creator_id", name="uq_data_exports_creator_id"),
    )
    op.create_index("ix_data_exports_creator_id", "data_exports", ["creator_id"])

    op.execute("ALTER TABLE data_exports ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE data_exports FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON data_exports;")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON data_exports
            USING (creator_id = current_setting('app.creator_id', true)::uuid)
            WITH CHECK (creator_id = current_setting('app.creator_id', true)::uuid);
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON data_exports;")
    op.execute("ALTER TABLE data_exports NO FORCE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE data_exports DISABLE ROW LEVEL SECURITY;")
    op.drop_index("ix_data_exports_creator_id", table_name="data_exports")
    op.drop_table("data_exports")
    op.execute("DROP TYPE IF EXISTS data_export_status_enum;")
