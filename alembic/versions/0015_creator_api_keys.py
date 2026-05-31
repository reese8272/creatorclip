"""Issue 95 — creator_api_keys table for the OBS companion app

Revision ID: 0015_creator_api_keys
Revises: 0014_backfill_onboarding_state

The companion app (`creatorclip-obs-companion`, separate repo) uploads
clips via API-key-authenticated POST /clips/ingest. We never store the
raw key — only a SHA-256 hex hash. A short prefix is stored for the
management UI so the user can identify a key without copying the raw
secret.

Revocation is soft (revoked_at set, row retained for audit).

Architecture: Issue 95 DECISIONS entry (2026-05-31).
"""

import sqlalchemy as sa

from alembic import op

revision = "0015_creator_api_keys"
down_revision = "0014_backfill_onboarding_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "creator_api_keys",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("creator_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("key_prefix", sa.String(length=8), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["creator_id"], ["creators.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_hash", name="uq_creator_api_keys_key_hash"),
    )
    op.create_index(
        "ix_creator_api_keys_creator_id",
        "creator_api_keys",
        ["creator_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_creator_api_keys_creator_id", table_name="creator_api_keys")
    op.drop_table("creator_api_keys")
