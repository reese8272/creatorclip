"""pgvector HNSW index + clip_feedback.creator_id index (Issue 65)

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-05-29

Issue 65 — index the access patterns that degrade O(rows) as data grows (scale
axis H).

  ix_dna_embeddings_hnsw — HNSW index on dna_embeddings.embedding using
    vector_cosine_ops (matches the `<=>` cosine query; voyage-3.5 vectors).
    HNSW is the recommended default for <10M vectors with active writes; IVFFlat
    is deliberately avoided here because its k-means clustering is data-dependent
    and must not live in a migration. Params m=16, ef_construction=200 (the
    documented "better recall" starting point above the 16/64 defaults).

  ix_clip_feedback_creator_id — clip_feedback.creator_id was an unindexed FK hit
    by the preference training query and the retrain debounce filter (Issue 60).

Note: dna_embeddings.creator_id (ix_dna_embeddings_creator_id, 0001) and
preference_models.creator_id (covered by the (creator_id, version) unique index)
are already indexed — no redundant indexes added.

Both indexes are built CONCURRENTLY inside an autocommit block because
CREATE INDEX CONCURRENTLY cannot run in Alembic's default transaction; this keeps
the build online-safe on populated tables.
"""

from alembic import op

revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_dna_embeddings_hnsw "
            "ON dna_embeddings USING hnsw (embedding vector_cosine_ops) "
            "WITH (m = 16, ef_construction = 200)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_clip_feedback_creator_id "
            "ON clip_feedback (creator_id)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_clip_feedback_creator_id")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_dna_embeddings_hnsw")
