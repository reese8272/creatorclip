"""Initial schema

Revision ID: a1b2c3d4e5f6
Revises:
Create Date: 2026-05-25

"""

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── pgvector extension ────────────────────────────────────────────────────
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ── Enums ─────────────────────────────────────────────────────────────────
    op.execute(
        "CREATE TYPE onboarding_state_enum AS ENUM "
        "('connected','awaiting_data','dna_pending','active')"
    )
    op.execute("CREATE TYPE video_kind_enum AS ENUM ('long','short')")
    op.execute("CREATE TYPE ingest_status_enum AS ENUM ('pending','running','done','failed')")
    op.execute("CREATE TYPE dna_status_enum AS ENUM ('draft','confirmed','superseded')")
    op.execute("CREATE TYPE dna_embedding_kind_enum AS ENUM ('pattern','clip','hook')")
    op.execute("CREATE TYPE clip_format_enum AS ENUM ('short','horizontal')")
    op.execute("CREATE TYPE render_status_enum AS ENUM ('pending','running','done','failed')")
    op.execute(
        "CREATE TYPE feedback_action_enum AS ENUM ('upvote','downvote','skip','trim','format')"
    )

    # ── creators ──────────────────────────────────────────────────────────────
    op.create_table(
        "creators",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("google_sub", sa.String(256), nullable=False),
        sa.Column("channel_id", sa.String(64), nullable=True),
        sa.Column("channel_title", sa.String(256), nullable=True),
        sa.Column("email", sa.String(256), nullable=True),
        sa.Column(
            "onboarding_state",
            sa.Enum(
                "connected",
                "awaiting_data",
                "dna_pending",
                "active",
                name="onboarding_state_enum",
                create_type=False,
            ),
            nullable=False,
            server_default="connected",
        ),
        sa.Column("plan_tier", sa.String(64), nullable=True),
        sa.Column("subscription_status", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("google_sub", name="uq_creators_google_sub"),
    )

    # ── youtube_tokens ────────────────────────────────────────────────────────
    op.create_table(
        "youtube_tokens",
        sa.Column(
            "creator_id",
            sa.Uuid,
            sa.ForeignKey("creators.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("access_token_encrypted", sa.Text, nullable=False),
        sa.Column("refresh_token_encrypted", sa.Text, nullable=False),
        sa.Column("scope", sa.Text, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # ── videos ────────────────────────────────────────────────────────────────
    op.create_table(
        "videos",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "creator_id", sa.Uuid, sa.ForeignKey("creators.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("youtube_video_id", sa.String(32), nullable=False),
        sa.Column("title", sa.Text, nullable=True),
        sa.Column(
            "kind",
            sa.Enum("long", "short", name="video_kind_enum", create_type=False),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_s", sa.Float, nullable=True),
        sa.Column("source_uri", sa.Text, nullable=True),
        sa.Column("captions_available", sa.Boolean, nullable=False, server_default="false"),
        sa.Column(
            "ingest_status",
            sa.Enum(
                "pending", "running", "done", "failed", name="ingest_status_enum", create_type=False
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("creator_id", "youtube_video_id", name="uq_creator_youtube_video"),
    )
    op.create_index("ix_videos_creator_id", "videos", ["creator_id"])
    op.create_index("ix_videos_ingest_status", "videos", ["ingest_status"])

    # ── video_metrics ─────────────────────────────────────────────────────────
    op.create_table(
        "video_metrics",
        sa.Column(
            "video_id", sa.Uuid, sa.ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("views", sa.BigInteger, nullable=True),
        sa.Column("watch_time_s", sa.BigInteger, nullable=True),
        sa.Column("avg_view_duration_s", sa.Float, nullable=True),
        sa.Column("engagement_rate", sa.Float, nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )

    # ── retention_curves ──────────────────────────────────────────────────────
    op.create_table(
        "retention_curves",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "video_id", sa.Uuid, sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("timestamp_s", sa.Float, nullable=False),
        sa.Column("audience_watch_ratio", sa.Float, nullable=False),
        sa.Column("relative_retention_performance", sa.Float, nullable=True),
        sa.Column("is_rewatch_spike", sa.Boolean, nullable=False, server_default="false"),
        sa.UniqueConstraint("video_id", "timestamp_s", name="uq_retention_curve_point"),
    )
    op.create_index("ix_retention_curves_video_id", "retention_curves", ["video_id"])

    # ── audience_activity ─────────────────────────────────────────────────────
    op.create_table(
        "audience_activity",
        sa.Column(
            "creator_id",
            sa.Uuid,
            sa.ForeignKey("creators.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("day_of_week", sa.SmallInteger, primary_key=True),
        sa.Column("hour", sa.SmallInteger, primary_key=True),
        sa.Column("activity_index", sa.Float, nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )

    # ── demographics ──────────────────────────────────────────────────────────
    op.create_table(
        "demographics",
        sa.Column(
            "creator_id",
            sa.Uuid,
            sa.ForeignKey("creators.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("payload_jsonb", JSONB, nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )

    # ── transcripts ───────────────────────────────────────────────────────────
    op.create_table(
        "transcripts",
        sa.Column(
            "video_id", sa.Uuid, sa.ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("segments_jsonb", JSONB, nullable=False),
    )

    # ── signals ───────────────────────────────────────────────────────────────
    op.create_table(
        "signals",
        sa.Column(
            "video_id", sa.Uuid, sa.ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("timeline_jsonb", JSONB, nullable=False),
    )

    # ── creator_dna ───────────────────────────────────────────────────────────
    op.create_table(
        "creator_dna",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "creator_id", sa.Uuid, sa.ForeignKey("creators.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("brief_text", sa.Text, nullable=True),
        sa.Column("patterns_jsonb", JSONB, nullable=True),
        sa.Column("top_video_ids_jsonb", JSONB, nullable=True),
        sa.Column("bottom_video_ids_jsonb", JSONB, nullable=True),
        sa.Column("optimal_clip_len_s", sa.Float, nullable=True),
        sa.Column("best_source_region", sa.String(64), nullable=True),
        sa.Column("optimal_upload_gap_h", sa.Float, nullable=True),
        sa.Column(
            "status",
            sa.Enum("draft", "confirmed", "superseded", name="dna_status_enum", create_type=False),
            nullable=False,
            server_default="draft",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("creator_id", "version", name="uq_dna_creator_version"),
    )
    op.create_index("ix_creator_dna_creator_id", "creator_dna", ["creator_id"])

    # ── dna_embeddings ────────────────────────────────────────────────────────
    op.create_table(
        "dna_embeddings",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "creator_id", sa.Uuid, sa.ForeignKey("creators.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "kind",
            sa.Enum("pattern", "clip", "hook", name="dna_embedding_kind_enum", create_type=False),
            nullable=False,
        ),
        sa.Column("embedding", Vector(1024), nullable=False),
        sa.Column("ref_jsonb", JSONB, nullable=True),
    )
    op.create_index("ix_dna_embeddings_creator_id", "dna_embeddings", ["creator_id"])

    # ── clips ─────────────────────────────────────────────────────────────────
    op.create_table(
        "clips",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "video_id", sa.Uuid, sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "creator_id", sa.Uuid, sa.ForeignKey("creators.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("setup_start_s", sa.Float, nullable=True),
        sa.Column("start_s", sa.Float, nullable=False),
        sa.Column("end_s", sa.Float, nullable=False),
        sa.Column("peak_s", sa.Float, nullable=True),
        sa.Column("score", sa.Float, nullable=True),
        sa.Column("dna_match", sa.Float, nullable=True),
        sa.Column("signals_jsonb", JSONB, nullable=True),
        sa.Column(
            "format",
            sa.Enum("short", "horizontal", name="clip_format_enum", create_type=False),
            nullable=False,
            server_default="short",
        ),
        sa.Column("render_uri", sa.Text, nullable=True),
        sa.Column(
            "render_status",
            sa.Enum(
                "pending", "running", "done", "failed", name="render_status_enum", create_type=False
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("rank", sa.Integer, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_clips_creator_id", "clips", ["creator_id"])
    op.create_index("ix_clips_video_id", "clips", ["video_id"])
    op.create_index("ix_clips_render_status", "clips", ["render_status"])

    # ── clip_feedback ─────────────────────────────────────────────────────────
    op.create_table(
        "clip_feedback",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "clip_id", sa.Uuid, sa.ForeignKey("clips.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "creator_id", sa.Uuid, sa.ForeignKey("creators.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "action",
            sa.Enum(
                "upvote",
                "downvote",
                "skip",
                "trim",
                "format",
                name="feedback_action_enum",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("trim_start_s", sa.Float, nullable=True),
        sa.Column("trim_end_s", sa.Float, nullable=True),
        sa.Column("chosen_format", sa.String(32), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_clip_feedback_clip_id", "clip_feedback", ["clip_id"])
    op.create_index("ix_clip_feedback_creator_id", "clip_feedback", ["creator_id"])

    # ── clip_outcomes ─────────────────────────────────────────────────────────
    op.create_table(
        "clip_outcomes",
        sa.Column(
            "clip_id", sa.Uuid, sa.ForeignKey("clips.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("published_youtube_id", sa.String(32), nullable=True),
        sa.Column("views", sa.BigInteger, nullable=True),
        sa.Column("retention", sa.Float, nullable=True),
        sa.Column("performed_well", sa.Boolean, nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )

    # ── preference_models ─────────────────────────────────────────────────────
    op.create_table(
        "preference_models",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "creator_id", sa.Uuid, sa.ForeignKey("creators.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("weights_blob", sa.LargeBinary, nullable=True),
        sa.Column("feature_schema_jsonb", JSONB, nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("creator_id", "version", name="uq_pref_model_creator_version"),
    )

    # ── usage ─────────────────────────────────────────────────────────────────
    op.create_table(
        "usage",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "creator_id", sa.Uuid, sa.ForeignKey("creators.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("period", sa.String(20), nullable=False),
        sa.Column("videos_processed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("clips_generated", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tokens_in", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.BigInteger, nullable=False, server_default="0"),
        sa.UniqueConstraint("creator_id", "period", name="uq_usage_creator_period"),
    )

    # ── audit_log (append-only) ───────────────────────────────────────────────
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("actor", sa.String(256), nullable=True),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=True),
        sa.Column("entity_id", sa.Uuid, nullable=True),
        sa.Column("before_jsonb", JSONB, nullable=True),
        sa.Column("after_jsonb", JSONB, nullable=True),
    )
    op.create_index("ix_audit_log_at", "audit_log", ["at"])
    op.create_index("ix_audit_log_entity", "audit_log", ["entity_type", "entity_id"])


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("usage")
    op.drop_table("preference_models")
    op.drop_table("clip_outcomes")
    op.drop_table("clip_feedback")
    op.drop_table("clips")
    op.drop_table("dna_embeddings")
    op.drop_table("creator_dna")
    op.drop_table("signals")
    op.drop_table("transcripts")
    op.drop_table("demographics")
    op.drop_table("audience_activity")
    op.drop_table("retention_curves")
    op.drop_table("video_metrics")
    op.drop_table("videos")
    op.drop_table("youtube_tokens")
    op.drop_table("creators")

    op.execute("DROP TYPE IF EXISTS feedback_action_enum")
    op.execute("DROP TYPE IF EXISTS render_status_enum")
    op.execute("DROP TYPE IF EXISTS clip_format_enum")
    op.execute("DROP TYPE IF EXISTS dna_embedding_kind_enum")
    op.execute("DROP TYPE IF EXISTS dna_status_enum")
    op.execute("DROP TYPE IF EXISTS ingest_status_enum")
    op.execute("DROP TYPE IF EXISTS video_kind_enum")
    op.execute("DROP TYPE IF EXISTS onboarding_state_enum")
