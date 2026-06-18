"""Add chat_conversations + chat_messages — Pro chatbot (Issue 152)

Revision ID: 0026
Revises: 0025
Create Date: 2026-06-17

Two tenant tables for the streaming Pro chatbot:

- ``chat_conversations`` has a direct ``creator_id`` → gets the same
  ``tenant_isolation`` RLS policy (ENABLE + FORCE) as the tables in
  0010_rls_policies, gating row visibility on
  ``current_setting('app.creator_id')``.
- ``chat_messages`` reaches its tenant via the ``conversation_id`` FK and is
  NOT given an explicit policy — the child-table pattern from 0010
  (video_metrics / clip_outcomes reach tenant via their parent's policy).
  The router additionally filters every read by the owning creator.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_conversations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "creator_id",
            UUID(as_uuid=True),
            sa.ForeignKey("creators.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(256), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_chat_conversations_creator_id", "chat_conversations", ["creator_id"])

    # chat_role_enum is used by only this one table, so let create_table emit the
    # single CREATE TYPE (plain sa.Enum, no explicit .create()) — the established
    # repo pattern (cf. video_kind_enum / ingest_status_enum in 0001). The earlier
    # explicit .create() on top of this was the second CREATE TYPE that raised
    # DuplicateObject.
    op.create_table(
        "chat_messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "conversation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("chat_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.Enum("user", "assistant", name="chat_role_enum"), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("tokens_in", sa.Integer, nullable=True),
        sa.Column("tokens_out", sa.Integer, nullable=True),
        sa.Column("cache_read", sa.Integer, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_chat_messages_conversation_id", "chat_messages", ["conversation_id"])

    # RLS on the parent only (child reaches tenant via FK) — mirrors 0010.
    op.execute("ALTER TABLE chat_conversations ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE chat_conversations FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON chat_conversations;")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON chat_conversations
            USING (creator_id = current_setting('app.creator_id', true)::uuid)
            WITH CHECK (creator_id = current_setting('app.creator_id', true)::uuid);
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON chat_conversations;")
    op.execute("ALTER TABLE chat_conversations NO FORCE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE chat_conversations DISABLE ROW LEVEL SECURITY;")
    op.drop_index("ix_chat_messages_conversation_id", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.execute("DROP TYPE IF EXISTS chat_role_enum;")
    op.drop_index("ix_chat_conversations_creator_id", table_name="chat_conversations")
    op.drop_table("chat_conversations")
