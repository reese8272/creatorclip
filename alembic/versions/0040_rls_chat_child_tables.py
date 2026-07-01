"""Add RLS to child tables without a direct creator_id column

Revision ID: 0040
Revises: 0039
Create Date: 2026-07-01

Closes the tenant-isolation gap found during Issue 348 (chat BYPASSRLS review).
Five tables store per-creator data via a foreign-key chain to a parent that
carries creator_id, rather than directly.  They were excluded from the 0010 RLS
rollout with a "belt-and-suspenders" note; this migration fills that gap.

Tables and their parent chain:
  video_metrics   → videos.creator_id
  retention_curves → videos.creator_id
  transcripts     → videos.creator_id
  clip_outcomes   → clips.creator_id
  chat_messages   → chat_conversations.creator_id

Each gets ENABLE + FORCE row-level security and a subquery-based tenant_isolation
policy.  The pattern mirrors 0010/0038 for the GUC predicate; the subquery form is
the standard for child tables that lack a direct creator_id (PostgreSQL docs §5.8).

Worker writes to video_metrics / retention_curves / transcripts / clip_outcomes use
AdminSessionLocal (BYPASSRLS) so WITH CHECK does not block ingestion.  chat_messages
are written by the chat worker via AsyncSessionLocal with the GUC set (Issue 348
fix), so WITH CHECK is actively enforced for that table.
"""

from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None

# Child tables: (table, parent_table, fk_column, parent_id_column, parent_creator_col)
_CHILD_TABLES: list[tuple[str, str, str]] = [
    ("video_metrics", "videos", "video_id"),
    ("retention_curves", "videos", "video_id"),
    ("transcripts", "videos", "video_id"),
    ("clip_outcomes", "clips", "clip_id"),
    ("chat_messages", "chat_conversations", "conversation_id"),
]


def _parent_table(entry: tuple[str, str, str]) -> str:
    return entry[1]


def _fk_col(entry: tuple[str, str, str]) -> str:
    return entry[2]


def upgrade() -> None:
    for table, parent, fk in _CHILD_TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO creatorclip_app;")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table};")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
                USING (
                    {fk} IN (
                        SELECT id FROM {parent}
                        WHERE creator_id = current_setting('app.creator_id', true)::uuid
                    )
                )
                WITH CHECK (
                    {fk} IN (
                        SELECT id FROM {parent}
                        WHERE creator_id = current_setting('app.creator_id', true)::uuid
                    )
                );
            """
        )


def downgrade() -> None:
    for table, _parent, _fk in _CHILD_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table};")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")
