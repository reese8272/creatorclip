"""Add event_logs table — beta telemetry sink (Issue 151)

Revision ID: 0025
Revises: 0024
Create Date: 2026-06-17

Append-only, high-volume UI + backend event log. Deliberately NOT given an RLS
policy (unlike tenant tables): it carries no tenant business data, reads are
isolated at the application layer (/api/logs/me filters by creator_id), and
operators must be able to query across all rows for beta analysis — mirroring
the audit_log exemption in 0010_rls_policies. Written only via
event_log.record_event(), which redacts PII/tokens before insert.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "event_logs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("event", sa.String(64), nullable=False),
        sa.Column("level", sa.String(16), nullable=False, server_default="info"),
        sa.Column("creator_id", UUID(as_uuid=True), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("page", sa.String(128), nullable=True),
        sa.Column("target", sa.String(256), nullable=True),
        sa.Column("status_code", sa.Integer, nullable=True),
        sa.Column("duration_ms", sa.Integer, nullable=True),
        sa.Column("extra", JSONB, nullable=True),
    )
    op.create_index("ix_event_logs_at", "event_logs", ["at"])
    op.create_index("ix_event_logs_event", "event_logs", ["event"])
    op.create_index("ix_event_logs_creator_id", "event_logs", ["creator_id"])


def downgrade() -> None:
    op.drop_index("ix_event_logs_creator_id", table_name="event_logs")
    op.drop_index("ix_event_logs_event", table_name="event_logs")
    op.drop_index("ix_event_logs_at", table_name="event_logs")
    op.drop_table("event_logs")
