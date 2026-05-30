"""
SQLAlchemy 2.0 models for CreatorClip.

Encrypted columns: access_token_encrypted / refresh_token_encrypted store Fernet
ciphertext. Always call crypto.encrypt() on write and crypto.decrypt() on read — never
access these columns raw in application logic.

Audit log: AuditLog rows must only be created via append_audit(). No UPDATE or DELETE
on this table from application code — ever.
"""

import enum
import uuid
from datetime import UTC, datetime
from typing import Optional

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base

# ── Enums ────────────────────────────────────────────────────────────────────


class OnboardingState(enum.Enum):
    connected = "connected"
    awaiting_data = "awaiting_data"
    dna_pending = "dna_pending"
    active = "active"


class VideoKind(enum.Enum):
    long = "long"
    short = "short"


class IngestStatus(enum.Enum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"


class DnaStatus(enum.Enum):
    draft = "draft"
    confirmed = "confirmed"
    superseded = "superseded"


class DnaEmbeddingKind(enum.Enum):
    pattern = "pattern"
    clip = "clip"
    hook = "hook"


class ClipFormat(enum.Enum):
    short = "short"
    horizontal = "horizontal"


class RenderStatus(enum.Enum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"


class FeedbackAction(enum.Enum):
    upvote = "upvote"
    downvote = "downvote"
    skip = "skip"
    trim = "trim"
    format = "format"


# ── Core entities ─────────────────────────────────────────────────────────────


class Creator(Base):
    __tablename__ = "creators"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    google_sub: Mapped[str] = mapped_column(sa.String(256), unique=True, nullable=False)
    channel_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    channel_title: Mapped[str | None] = mapped_column(sa.String(256), nullable=True)
    email: Mapped[str | None] = mapped_column(sa.String(256), nullable=True)
    onboarding_state: Mapped[OnboardingState] = mapped_column(
        sa.Enum(OnboardingState, name="onboarding_state_enum"),
        nullable=False,
        default=OnboardingState.connected,
    )
    stripe_customer_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    minutes_balance: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    last_analytics_refreshed_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=True,
    )

    tokens: Mapped[Optional["YoutubeToken"]] = relationship(
        "YoutubeToken", back_populates="creator", uselist=False, cascade="all, delete-orphan"
    )
    videos: Mapped[list["Video"]] = relationship(
        "Video", back_populates="creator", cascade="all, delete-orphan"
    )
    dna_profiles: Mapped[list["CreatorDna"]] = relationship(
        "CreatorDna", back_populates="creator", cascade="all, delete-orphan"
    )


class YoutubeToken(Base):
    __tablename__ = "youtube_tokens"

    creator_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("creators.id", ondelete="CASCADE"), primary_key=True
    )
    # Fernet-encrypted — always use crypto.encrypt() / crypto.decrypt()
    access_token_encrypted: Mapped[str] = mapped_column(sa.Text, nullable=False)
    refresh_token_encrypted: Mapped[str] = mapped_column(sa.Text, nullable=False)
    scope: Mapped[str] = mapped_column(sa.Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    creator: Mapped["Creator"] = relationship("Creator", back_populates="tokens")


# ── Video & analytics ─────────────────────────────────────────────────────────


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    creator_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("creators.id", ondelete="CASCADE"), nullable=False
    )
    youtube_video_id: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    title: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    kind: Mapped[VideoKind] = mapped_column(
        sa.Enum(VideoKind, name="video_kind_enum"), nullable=False
    )
    published_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    duration_s: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    source_uri: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    captions_available: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    ingest_status: Mapped[IngestStatus] = mapped_column(
        sa.Enum(IngestStatus, name="ingest_status_enum"),
        nullable=False,
        default=IngestStatus.pending,
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    ingest_done_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        sa.UniqueConstraint("creator_id", "youtube_video_id", name="uq_creator_youtube_video"),
    )

    creator: Mapped["Creator"] = relationship("Creator", back_populates="videos")
    metrics: Mapped[Optional["VideoMetrics"]] = relationship(
        "VideoMetrics", back_populates="video", uselist=False, cascade="all, delete-orphan"
    )
    retention_curves: Mapped[list["RetentionCurve"]] = relationship(
        "RetentionCurve", back_populates="video", cascade="all, delete-orphan"
    )
    transcript: Mapped[Optional["Transcript"]] = relationship(
        "Transcript", back_populates="video", uselist=False, cascade="all, delete-orphan"
    )
    signals: Mapped[Optional["Signals"]] = relationship(
        "Signals", back_populates="video", uselist=False, cascade="all, delete-orphan"
    )
    clips: Mapped[list["Clip"]] = relationship(
        "Clip", back_populates="video", cascade="all, delete-orphan"
    )


class VideoMetrics(Base):
    __tablename__ = "video_metrics"

    video_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True
    )
    views: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    watch_time_s: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    avg_view_duration_s: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    engagement_rate: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)

    video: Mapped["Video"] = relationship("Video", back_populates="metrics")


class RetentionCurve(Base):
    __tablename__ = "retention_curves"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    video_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
    )
    timestamp_s: Mapped[float] = mapped_column(sa.Float, nullable=False)
    audience_watch_ratio: Mapped[float] = mapped_column(sa.Float, nullable=False)
    relative_retention_performance: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    is_rewatch_spike: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)

    __table_args__ = (
        sa.UniqueConstraint("video_id", "timestamp_s", name="uq_retention_curve_point"),
    )

    video: Mapped["Video"] = relationship("Video", back_populates="retention_curves")


class AudienceActivity(Base):
    __tablename__ = "audience_activity"

    creator_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("creators.id", ondelete="CASCADE"), primary_key=True
    )
    day_of_week: Mapped[int] = mapped_column(sa.SmallInteger, primary_key=True)  # 0=Sunday
    hour: Mapped[int] = mapped_column(sa.SmallInteger, primary_key=True)  # 0–23
    activity_index: Mapped[float] = mapped_column(sa.Float, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)


class Demographics(Base):
    __tablename__ = "demographics"

    creator_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("creators.id", ondelete="CASCADE"), primary_key=True
    )
    payload_jsonb: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)


# ── Ingestion ─────────────────────────────────────────────────────────────────


class Transcript(Base):
    __tablename__ = "transcripts"

    video_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True
    )
    source: Mapped[str] = mapped_column(sa.String(50), nullable=False)  # whisperx/captions/hosted
    segments_jsonb: Mapped[dict] = mapped_column(JSONB, nullable=False)

    video: Mapped["Video"] = relationship("Video", back_populates="transcript")


class Signals(Base):
    __tablename__ = "signals"

    video_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True
    )
    timeline_jsonb: Mapped[dict] = mapped_column(JSONB, nullable=False)

    video: Mapped["Video"] = relationship("Video", back_populates="signals")


# ── Creator DNA ───────────────────────────────────────────────────────────────


class CreatorDna(Base):
    __tablename__ = "creator_dna"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    creator_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("creators.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    brief_text: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    patterns_jsonb: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    top_video_ids_jsonb: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    bottom_video_ids_jsonb: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    optimal_clip_len_s: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    best_source_region: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    optimal_upload_gap_h: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    # Celery task id of the build that created this draft — the idempotency key for
    # at-least-once redelivery (Issue 63). Nullable: legacy rows + non-task callers.
    build_job_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    status: Mapped[DnaStatus] = mapped_column(
        sa.Enum(DnaStatus, name="dna_status_enum"),
        nullable=False,
        default=DnaStatus.draft,
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    __table_args__ = (
        sa.UniqueConstraint("creator_id", "version", name="uq_dna_creator_version"),
        # Partial UNIQUE on the Celery idempotency key: at most one draft per build
        # job id. Structural backstop for the advisory-lock guard in build_dna so a
        # concurrent same-task redelivery cannot persist a second draft (Issue 76).
        # Also serves the idempotency lookup, replacing the plain index from 0005.
        sa.Index(
            "uq_creator_dna_build_job_id",
            "build_job_id",
            unique=True,
            postgresql_where=sa.text("build_job_id IS NOT NULL"),
        ),
    )

    creator: Mapped["Creator"] = relationship("Creator", back_populates="dna_profiles")


class DnaEmbedding(Base):
    __tablename__ = "dna_embeddings"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    creator_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("creators.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[DnaEmbeddingKind] = mapped_column(
        sa.Enum(DnaEmbeddingKind, name="dna_embedding_kind_enum"), nullable=False
    )
    embedding: Mapped[list] = mapped_column(Vector(1024), nullable=False)  # voyage-3.5 dims
    ref_jsonb: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


# ── Clips ─────────────────────────────────────────────────────────────────────


class Clip(Base):
    __tablename__ = "clips"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    video_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
    )
    creator_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("creators.id", ondelete="CASCADE"), nullable=False
    )
    setup_start_s: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    start_s: Mapped[float] = mapped_column(sa.Float, nullable=False)
    end_s: Mapped[float] = mapped_column(sa.Float, nullable=False)
    peak_s: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    score: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    dna_match: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    signals_jsonb: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    format: Mapped[ClipFormat] = mapped_column(
        sa.Enum(ClipFormat, name="clip_format_enum"),
        nullable=False,
        default=ClipFormat.short,
    )
    render_uri: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    render_status: Mapped[RenderStatus] = mapped_column(
        sa.Enum(RenderStatus, name="render_status_enum"),
        nullable=False,
        default=RenderStatus.pending,
    )
    rank: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    video: Mapped["Video"] = relationship("Video", back_populates="clips")
    feedback: Mapped[list["ClipFeedback"]] = relationship(
        "ClipFeedback", back_populates="clip", cascade="all, delete-orphan"
    )
    outcome: Mapped[Optional["ClipOutcome"]] = relationship(
        "ClipOutcome", back_populates="clip", uselist=False, cascade="all, delete-orphan"
    )


class ClipFeedback(Base):
    __tablename__ = "clip_feedback"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    clip_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("clips.id", ondelete="CASCADE"), nullable=False
    )
    creator_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("creators.id", ondelete="CASCADE"), nullable=False
    )
    action: Mapped[FeedbackAction] = mapped_column(
        sa.Enum(FeedbackAction, name="feedback_action_enum"), nullable=False
    )
    trim_start_s: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    trim_end_s: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    chosen_format: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    clip: Mapped["Clip"] = relationship("Clip", back_populates="feedback")


class ClipOutcome(Base):
    __tablename__ = "clip_outcomes"

    clip_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("clips.id", ondelete="CASCADE"), primary_key=True
    )
    published_youtube_id: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
    views: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    retention: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    performed_well: Mapped[bool | None] = mapped_column(sa.Boolean, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    # Terminal marker: once the 7d checkpoint is recorded the outcome is never
    # re-polled (bounds the YouTube-quota drain). (Issue 70)
    final: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)

    clip: Mapped["Clip"] = relationship("Clip", back_populates="outcome")


# ── Preference model ──────────────────────────────────────────────────────────


class PreferenceModel(Base):
    __tablename__ = "preference_models"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    creator_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("creators.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    weights_blob: Mapped[bytes | None] = mapped_column(sa.LargeBinary, nullable=True)
    feature_schema_jsonb: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)

    __table_args__ = (
        sa.UniqueConstraint("creator_id", "version", name="uq_pref_model_creator_version"),
    )


# ── Billing ───────────────────────────────────────────────────────────────────


class MinutePack(Base):
    """Immutable record of every minute grant — trial, purchase, or manual."""

    __tablename__ = "minute_packs"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    creator_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("creators.id", ondelete="CASCADE"), nullable=False
    )
    pack_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    minutes_granted: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    price_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    stripe_session_id: Mapped[str | None] = mapped_column(
        sa.String(128), nullable=True, unique=True
    )
    reason: Mapped[str] = mapped_column(sa.String(64), nullable=False)  # "trial" | "purchase"
    granted_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class MinuteDeduction(Base):
    """Immutable record of every minute deduction — keyed UNIQUE on video_id.

    The UNIQUE(video_id) constraint is the idempotency key: Celery's at-least-once
    delivery (with task_acks_late=True) can re-invoke an ingest task after the
    deduction commits, and the constraint prevents a second deduction from inserting.
    See docs/DECISIONS.md 2026-05-28 entry on per-video idempotency.
    """

    __tablename__ = "minute_deductions"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    video_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("videos.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    creator_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("creators.id", ondelete="CASCADE"), nullable=False
    )
    minutes_deducted: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    duration_s: Mapped[float] = mapped_column(sa.Float, nullable=False)
    deducted_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


# ── Usage & audit ─────────────────────────────────────────────────────────────


class Usage(Base):
    __tablename__ = "usage"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    creator_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("creators.id", ondelete="CASCADE"), nullable=False
    )
    period: Mapped[str] = mapped_column(sa.String(20), nullable=False)  # e.g., "2026-05"
    videos_processed: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    clips_generated: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    tokens_in: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=0)

    __table_args__ = (sa.UniqueConstraint("creator_id", "period", name="uq_usage_creator_period"),)


class AuditLog(Base):
    """Append-only. Use append_audit() — never UPDATE or DELETE from application code."""

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    actor: Mapped[str | None] = mapped_column(sa.String(256), nullable=True)
    action: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, nullable=True)
    before_jsonb: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    after_jsonb: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


# ── Helper ────────────────────────────────────────────────────────────────────


async def append_audit(
    session,
    action: str,
    actor: str | None = None,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    before: dict | None = None,
    after: dict | None = None,
) -> None:
    session.add(
        AuditLog(
            actor=actor,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            before_jsonb=before,
            after_jsonb=after,
        )
    )


# ── Improvement brief (async 202 + poll) ──────────────────────────────────────


class ImprovementBriefStatus(enum.Enum):
    pending = "pending"
    ready = "ready"
    failed = "failed"


class ImprovementBrief(Base):
    """Async-generated content-improvement brief for a creator (Issue 78d).

    One row per creator. The POST endpoint resets it to ``pending`` and enqueues a
    Celery task; the task runs the ~120s Claude + web_search call and writes
    ``brief_text``/``status``; the GET endpoint polls this row. Mirrors the
    DNA-build 202 + poll precedent so the long call never sits on the request path.
    """

    __tablename__ = "improvement_briefs"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    creator_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("creators.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[ImprovementBriefStatus] = mapped_column(
        sa.Enum(ImprovementBriefStatus, name="improvement_brief_status"),
        nullable=False,
        default=ImprovementBriefStatus.pending,
    )
    brief_text: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    # Safe, user-facing failure message only — never a stack trace or token/PII.
    error: Mapped[str | None] = mapped_column(sa.String(256), nullable=True)
    # Celery task id of the in-flight / last build — the idempotency handle.
    job_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
