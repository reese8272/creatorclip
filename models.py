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

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base

# ── Enums ────────────────────────────────────────────────────────────────────


class OnboardingState(enum.Enum):
    connected = "connected"
    awaiting_data = "awaiting_data"
    dna_pending = "dna_pending"
    active = "active"


class AnalysisMode(enum.Enum):
    # Auto: new linked videos ingest immediately (current implicit behavior).
    # Selective: linked videos sit in the catalog until the creator explicitly
    #   queues each one. Manual: only creator-uploaded files are processed;
    #   the YouTube-link path remains available but mirrors Selective semantics.
    # See docs/DECISIONS.md Issue 125.
    auto = "auto"
    selective = "selective"
    manual = "manual"


class VideoKind(enum.Enum):
    long = "long"
    short = "short"


class VideoOrigin(enum.Enum):
    """How a Video row entered the system — the canonical provenance discriminator.

    ``catalog`` rows are DNA/analytics-only references upserted by
    ``sync_video_catalog`` from the creator's uploads playlist (no stored media,
    excluded from the dashboard list). ``link`` rows are registered by ID via
    ``POST /videos/link`` (also no stored media — the creator must upload the
    source file to clip, per YouTube ToS we never download it). ``upload`` rows
    carry stored source media (``source_uri``) and are the only clip-trackable
    path. Replaces the prior ``source_uri IS NULL`` heuristic, which wrongly hid
    linked videos (Issue 139)."""

    catalog = "catalog"
    link = "link"
    upload = "upload"


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


class PublishStatus(enum.Enum):
    """Lifecycle of a YouTube publish attempt (Issue 195 / 196).

    ``scheduled`` — row created but not yet confirmed by the creator.
    ``confirmed`` — creator approved; the Beat sweep will enqueue the upload.
    ``pending``   — enqueued to Celery but not yet picked up.
    ``running``   — the upload is in flight.
    ``done``      — youtube_video_id returned and stored.
    ``failed``    — permanent error recorded in ``error`` column.
    """

    scheduled = "scheduled"
    confirmed = "confirmed"
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"


class PublishPlatform(enum.Enum):
    """Distribution platform for a scheduled ClipPublication (Issue 196).

    Only YouTube is supported in this release. Additional platforms (TikTok,
    Instagram Reels, etc.) are deferred — tracked in docs/issues.md research
    finding 13.
    """

    youtube = "youtube"


class FeedbackAction(enum.Enum):
    upvote = "upvote"
    downvote = "downvote"
    skip = "skip"
    trim = "trim"
    format = "format"


class InsightType(enum.Enum):
    performer_analysis = "performer_analysis"
    trend = "trend"
    recommendation = "recommendation"


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
    analysis_mode: Mapped[AnalysisMode] = mapped_column(
        sa.Enum(AnalysisMode, name="analysis_mode_enum"),
        nullable=False,
        default=AnalysisMode.auto,
        server_default=AnalysisMode.auto.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    last_analytics_refreshed_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=True,
    )
    # Issue 126 — set on first OAuth login (auth.py), to `now + TRIAL_DURATION_DAYS`.
    # NULL on legacy rows that predate the migration; the trial-active predicate
    # treats NULL as "no trial" so legacy creators with a purchased balance keep
    # working unchanged. The 402 paywall in billing/ledger.py reads this live.
    trial_ends_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=True,
    )

    # Issue 299 — Clickwrap consent record.
    # Affirmative ToS/Privacy checkbox on the Login page gates the OAuth CTA.
    # The timestamp + version strings recorded here are the defensible consent
    # artifact (Chabolla v. ClassPass 9th Cir. 2025; GDPR Art. 7).
    # NULL on legacy rows that predate migration 0033 — treated as "no recorded
    # consent" for audit purposes; re-prompt logic can check for NULL.
    terms_accepted_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=True,
    )
    terms_version: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
    privacy_version: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)

    # Issue 300 — COPPA 13+ minimum-age attestation.
    # A separate affirmative "I confirm I am 13 or older" checkbox is shown
    # alongside the Issue 299 consent checkbox and must be checked before the
    # OAuth CTA becomes active.  The timestamp here is the audit record.
    # Age-neutral phrasing ("13 or older") is the FTC-recommended pattern per the
    # amended COPPA Rule (16 CFR Part 312, effective 2025-06-23) — it avoids a
    # yes/no question that nudges the answer.  NULL on legacy rows that predate
    # migration 0034; a future flag can check for NULL to re-prompt.
    minimum_age_confirmed_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=True,
    )

    tokens: Mapped["YoutubeToken | None"] = relationship(
        "YoutubeToken", back_populates="creator", uselist=False, cascade="all, delete-orphan"
    )
    videos: Mapped[list["Video"]] = relationship(
        "Video", back_populates="creator", cascade="all, delete-orphan"
    )
    dna_profiles: Mapped[list["CreatorDna"]] = relationship(
        "CreatorDna", back_populates="creator", cascade="all, delete-orphan"
    )
    api_keys: Mapped[list["CreatorApiKey"]] = relationship(
        "CreatorApiKey", back_populates="creator", cascade="all, delete-orphan"
    )


# ── API key auth (Issue 95 — OBS companion app + folder watcher) ───────────


class CreatorApiKey(Base):
    """API key for the OBS companion app and any future non-browser client.

    The companion app authenticates uploads to /clips/ingest with
    Authorization: Bearer <api_key>. We NEVER store the raw key — only a
    SHA-256 hex hash. The raw key is shown to the user ONCE at creation
    time. A short ``key_prefix`` is stored for display so the user can
    identify a key in the management UI without copying it.

    Revocation is soft (revoked_at set, row stays for audit). Lookups
    filter ``revoked_at IS NULL`` so revoked keys deterministically fail
    authentication.

    Issue 95 / 2026-05-31 — see docs/DECISIONS.md for architecture context.
    """

    __tablename__ = "creator_api_keys"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    creator_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("creators.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    # SHA-256 hex = 64 chars. UNIQUE so two keys can never collide.
    key_hash: Mapped[str] = mapped_column(sa.String(64), unique=True, nullable=False)
    # First 8 chars of the raw key (post-prefix) for display in the
    # management UI. Safe to store — it's not enough to authenticate.
    key_prefix: Mapped[str] = mapped_column(sa.String(8), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    last_used_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)

    creator: Mapped["Creator"] = relationship("Creator", back_populates="api_keys")


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
    # Nullable since Issue 317: a standalone raw-file upload has no published
    # YouTube video to point at. The (creator_id, youtube_video_id) unique
    # constraint still holds — Postgres treats NULLs as distinct, so any number
    # of un-associated uploads coexist per creator.
    youtube_video_id: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
    title: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    kind: Mapped[VideoKind] = mapped_column(
        sa.Enum(VideoKind, name="video_kind_enum"), nullable=False
    )
    published_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    duration_s: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    source_uri: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    # The extracted audio WAV (transcribe + signals read this). Kept separate from
    # `source_uri` (the original video) so ingest no longer clobbers the video the
    # renderer needs — see migration 0039. NULL until ingest extracts audio.
    audio_uri: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    origin: Mapped[VideoOrigin] = mapped_column(
        sa.Enum(VideoOrigin, name="video_origin_enum"),
        nullable=False,
        default=VideoOrigin.upload,
    )
    captions_available: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    ingest_status: Mapped[IngestStatus] = mapped_column(
        sa.Enum(IngestStatus, name="ingest_status_enum"),
        nullable=False,
        default=IngestStatus.pending,
    )
    # A short, creator-safe explanation set when ingest_status flips to failed, so
    # the dashboard can show WHY instead of a bare "FAILED" badge that needs a log
    # dive. Cleared on a successful re-run. Never holds a stack trace or any secret
    # — the worker maps the exception to a humanized reason before storing.
    failure_reason: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
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
    metrics: Mapped["VideoMetrics | None"] = relationship(
        "VideoMetrics", back_populates="video", uselist=False, cascade="all, delete-orphan"
    )
    retention_curves: Mapped[list["RetentionCurve"]] = relationship(
        "RetentionCurve", back_populates="video", cascade="all, delete-orphan"
    )
    transcript: Mapped["Transcript | None"] = relationship(
        "Transcript", back_populates="video", uselist=False, cascade="all, delete-orphan"
    )
    signals: Mapped["Signals | None"] = relationship(
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


class CreatorIdentity(Base):
    """Append-only versioned record of a creator's self-described identity (Issue 83).

    Captures who the creator says they are, who they're for, and what they
    won't do. Fused with the inferred ``CreatorDna`` at clip-engine and
    brief-generation time, kept structurally separate so the two signals
    can be reconciled honestly (see ``dna/conflict.py``).

    Lifecycle: each ``POST /creators/me/identity`` creates a new row and
    stamps ``superseded_at`` on the prior current row inside one
    transaction. The partial unique index
    ``uq_one_current_identity_per_creator`` is the DB-level guarantee that
    only ONE row per creator has ``superseded_at IS NULL`` at any moment.
    """

    __tablename__ = "creator_identity"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    creator_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("creators.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    # JSONB array of YouTube Data API category IDs (strings, e.g. ["27", "26"]).
    # See youtube/categories.py for the stable enum mapping.
    niches: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    audience_summary: Mapped[str] = mapped_column(sa.Text, nullable=False)
    content_pillars: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    tone_tags: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    hard_nos: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    mission: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    style_sample: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    # NULL means current. Stamped non-null when superseded by a newer version.
    superseded_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        sa.UniqueConstraint("creator_id", "version", name="uq_identity_creator_version"),
    )


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


# ── Creator Brand Kit (Issue 186) ─────────────────────────────────────────────


class CreatorStyle(Base):
    """One row per creator storing their brand-kit render style defaults.

    All style fields live in a JSONB `style` column so adding new style
    options never requires a migration. MutableDict.as_mutable() ensures
    in-place dict mutations (e.g. `row.style['subtitle'] = 'bold_pop'`)
    are tracked by SQLAlchemy's unit-of-work without a re-assign.

    Keys currently used by the render pipeline:
        subtitle         : str | None   — caption style id
        background       : str | None   — background fill ("blur"|"black")
        captions_enabled : bool
        zoom_on_peak     : bool
        denoise          : bool
        aspect           : str | None   — "9:16" | "1:1" | "16:9"
    """

    __tablename__ = "creator_style"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    creator_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("creators.id", ondelete="CASCADE"),
        nullable=False,
    )
    style: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSONB()),
        nullable=False,
        default=dict,
        server_default=sa.text("'{}'::jsonb"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    __table_args__ = (sa.UniqueConstraint("creator_id", name="uq_creator_style_creator_id"),)


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
    # Cleaned (filler+silence removed) render variant (Issue 134). When set,
    # the UI offers a "use cleaned version" affordance; POST /clean/confirm
    # swaps this into render_uri and clears the field. Independent of
    # render_status, which still tracks the original render's progress.
    cleaned_render_uri: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    render_status: Mapped[RenderStatus] = mapped_column(
        sa.Enum(RenderStatus, name="render_status_enum"),
        nullable=False,
        default=RenderStatus.pending,
    )
    rank: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    # Render style chosen by the creator in the review UI (Issue 119).
    # JSONB: {subtitle: "white_large"|"yellow_impact"|"captions_sm"|null,
    #         background: "blur"|"black"|"brand"|null, captions_enabled: bool}
    style_preset: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    video: Mapped["Video"] = relationship("Video", back_populates="clips")
    feedback: Mapped[list["ClipFeedback"]] = relationship(
        "ClipFeedback", back_populates="clip", cascade="all, delete-orphan"
    )
    outcome: Mapped["ClipOutcome | None"] = relationship(
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
    # Structured feedback tags (Issue 118). JSONB list of tag strings e.g.
    # ["titles_fit_style", "good_hook"] for approve or ["wrong_length"] for deny.
    feedback_tags: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # Free-text "Other" field captured alongside tags (Issue 118).
    feedback_note: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
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


class ClipImpression(Base):
    """Per-creator impression/position log (Issue 202).

    Records what RANK each clip was shown at, and WHEN, every time a creator's clip
    list is served. This is the position record that counterfactual/IPS evaluation
    needs; capturing it now is cheap insurance — it cannot be reconstructed later.

    No PII, no YouTube-origin data — only internal ids, an integer rank, and a
    timestamp. Per-creator isolation is enforced by the ``tenant_isolation`` RLS
    policy on ``creator_id`` (migration 0037); the FK cascade purges rows on account
    deletion (right-to-erasure).
    """

    __tablename__ = "clip_impressions"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    creator_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("creators.id", ondelete="CASCADE"), nullable=False
    )
    clip_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("clips.id", ondelete="CASCADE"), nullable=False
    )
    rank: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    shown_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class ClipPublication(Base):
    """A YouTube publish attempt or scheduled publication for a clip (Issues 195/196).

    Idempotency: ``task_id`` (the Celery task id) is UNIQUE — an at-least-once
    redelivery finds the existing row instead of double-posting. The returned
    ``youtube_video_id`` is stored before the task acks.

    Scheduling fields (Issue 196):
    - ``scheduled_at``  — target publish datetime (UTC); NULL = immediate on enqueue.
    - ``platform``      — target distribution platform (default: youtube).
    - ``confirmed_at``  — when the creator confirmed the schedule; NULL until confirmed.

    Status lifecycle:
      scheduled → confirmed (creator approves) → pending (enqueued by Beat sweep)
      → running → done | failed

    Per-creator isolation via the ``tenant_isolation`` RLS policy on ``creator_id``.
    """

    __tablename__ = "clip_publications"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    clip_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("clips.id", ondelete="CASCADE"), nullable=False
    )
    creator_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("creators.id", ondelete="CASCADE"), nullable=False
    )
    # Celery task id — UNIQUE so a redelivered publish task is idempotent.
    # NULL until the Beat sweep enqueues the upload (status moves pending→running).
    task_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True, unique=True)
    youtube_video_id: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
    status: Mapped[PublishStatus] = mapped_column(
        sa.Enum(PublishStatus, name="publish_status_enum"),
        nullable=False,
        default=PublishStatus.scheduled,
    )
    error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    # ── Scheduling fields (Issue 196) ─────────────────────────────────────────
    # scheduled_at: the creator's chosen publish time (UTC). The Beat sweep
    # enqueues the upload when scheduled_at <= now() AND status=confirmed.
    # NULL is valid for rows created directly as pending (immediate publish).
    scheduled_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    platform: Mapped[PublishPlatform] = mapped_column(
        sa.Enum(PublishPlatform, name="publish_platform_enum"),
        nullable=False,
        default=PublishPlatform.youtube,
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )


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
    # Cost estimate in USD persisted at write time so billing/metrics can read USD
    # without a price-book join at query time. Added by migration 0028. (Issue 220)
    cost_estimate: Mapped[float | None] = mapped_column(
        sa.Numeric(precision=12, scale=6), nullable=True
    )

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
    # none_as_null=True so a Python ``None`` payload is stored as SQL NULL, not
    # the JSONB ``'null'`` literal (the SQLAlchemy JSON default). Without this,
    # ``before=None``/``after=None`` writes a non-SQL-NULL value, which breaks
    # ``IS NULL`` filters and — for the never-purged ``creator.deleted`` audit
    # (Issue 247, GDPR Art. 17) — muddies the "no PII payload retained" invariant.
    before_jsonb: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    after_jsonb: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), nullable=True)


class EventLog(Base):
    """High-volume beta telemetry: UI events (click/submit/navigate) and backend
    events (http_request, task milestones). Append-only — written ONLY via
    event_log.record_event(), which redacts PII/tokens at the boundary.

    Distinct from AuditLog (transactional security/data-change trail with
    before/after state): this is behavioural telemetry for beta analysis, not a
    compliance audit trail. No RLS policy — it carries no tenant business data,
    reads are isolated at the application layer (a creator sees only their own
    rows via /api/logs/me); operators query the table directly. See Issue 151 +
    docs/DECISIONS.md (2026-06-17)."""

    __tablename__ = "event_logs"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        index=True,
    )
    source: Mapped[str] = mapped_column(sa.String(16), nullable=False)  # "ui" | "backend"
    event: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    level: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="info")
    # Nullable: anonymous/pre-login UI events and system backend events have no creator.
    creator_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, nullable=True, index=True)
    request_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    page: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)
    target: Mapped[str | None] = mapped_column(sa.String(256), nullable=True)
    status_code: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    extra: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


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

    __table_args__ = (
        # One row per creator; the DB-level backstop for the concurrent-first-insert race
        # that SELECT FOR UPDATE SKIP LOCKED cannot prevent (no row → no lock to acquire).
        # The router's IntegrityError catch re-queries and returns the winning row.
        sa.UniqueConstraint("creator_id", name="uq_improvement_briefs_creator_id"),
    )


class DataExportStatus(enum.Enum):
    pending = "pending"
    ready = "ready"
    failed = "failed"


class DataExport(Base):
    """Async GDPR Art. 15/20 data export for a creator (Issue 249).

    One row per creator. The POST endpoint resets it to ``pending`` and enqueues
    a Celery task; the task gathers every data class into a JSON artifact, uploads
    it to R2, and writes ``export_uri``/``status``; the GET endpoint polls this row
    and returns a short-lived presigned download link. Mirrors the
    improvement-brief 202 + poll precedent.
    """

    __tablename__ = "data_exports"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    creator_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("creators.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[DataExportStatus] = mapped_column(
        sa.Enum(DataExportStatus, name="data_export_status_enum"),
        nullable=False,
        default=DataExportStatus.pending,
    )
    # s3:// URI of the generated JSON artifact (None until ready).
    export_uri: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    error: Mapped[str | None] = mapped_column(sa.String(256), nullable=True)
    job_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)

    __table_args__ = (sa.UniqueConstraint("creator_id", name="uq_data_exports_creator_id"),)


# ── Creator insights (Issue 117) ──────────────────────────────────────────────


class CreatorInsight(Base):
    """AI-generated per-performer or channel-level insight.

    Generated lazily on demand (creator clicks "Analyze") using Haiku 4.5.
    Cached per (video_id, dna_version) so the same analysis is served until
    the DNA changes. Creators can save/bookmark insights for later reference.
    """

    __tablename__ = "creator_insights"
    __table_args__ = (
        # Composite index for the cache-lookup query:
        # WHERE creator_id = ? AND video_id = ? AND insight_type = ? AND dna_version = ?
        # Without this, the query scans all insights for the creator. (Issue 123)
        sa.Index(
            "ix_creator_insight_creator_video",
            "creator_id",
            "video_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    creator_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("creators.id", ondelete="CASCADE"), nullable=False, index=True
    )
    video_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("videos.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    insight_type: Mapped[InsightType] = mapped_column(
        sa.Enum(InsightType, name="insight_type_enum"), nullable=False
    )
    title: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    content: Mapped[str] = mapped_column(sa.Text, nullable=False)
    dna_version: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    is_saved: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class ChatRole(enum.Enum):
    """Author of a chat message. Mirrors the Anthropic Messages API roles we
    persist — tool-use / tool-result turns are NOT stored as their own rows;
    they are reconstructed live inside a single assistant turn (Issue 152)."""

    user = "user"
    assistant = "assistant"


class ChatConversation(Base):
    """A Pro-chatbot conversation thread, scoped to one creator (Issue 152).

    Carries a direct ``creator_id`` so it sits behind the RLS ``tenant_isolation``
    policy (migration 0026) AND is filtered explicitly at the app layer in
    routers/chat.py — defense in depth, mirroring the improvement-brief fix.
    """

    __tablename__ = "chat_conversations"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    creator_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("creators.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str | None] = mapped_column(sa.String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    messages: Mapped[list["ChatMessage"]] = relationship(
        "ChatMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ChatMessage.created_at",
    )


class ChatMessage(Base):
    """One user or assistant turn in a conversation (Issue 152).

    Reaches its tenant via the ``conversation_id`` FK to chat_conversations
    (which is RLS-gated) — child-table pattern, no direct policy, mirroring
    video_metrics / clip_outcomes in migration 0010. Token counts on assistant
    rows feed the per-message cost log (the One Rule; honesty on spend).
    """

    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("chat_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[ChatRole] = mapped_column(sa.Enum(ChatRole, name="chat_role_enum"), nullable=False)
    content: Mapped[str] = mapped_column(sa.Text, nullable=False)
    # Token accounting on assistant rows only (NULL on user rows). Summed across
    # the whole tool-loop turn so the cost log reflects real spend per message.
    tokens_in: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    cache_read: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    conversation: Mapped["ChatConversation"] = relationship(
        "ChatConversation", back_populates="messages"
    )


# ── Notifications (Issue 243) ─────────────────────────────────────────────────


class NotificationChannel(enum.Enum):
    """Delivery channel for a notification_deliveries row."""

    email = "email"
    inapp = "inapp"
    push = "push"


class NotificationDeliveryStatus(enum.Enum):
    """Terminal or intermediate state of a single delivery attempt."""

    sent = "sent"
    skipped = "skipped"
    failed = "failed"


class NotificationPreference(Base):
    """Per-creator consent and channel opt-out state (Issue 243).

    One row per creator, created lazily on first send.  The ``email_transactional``
    column is always-on (legally required for true transactional mail under
    CAN-SPAM and GDPR legitimate-interest) — the UI shows it but disables the
    toggle.  ``email_lifecycle`` is the unsubscribable category (welcome / nudge /
    re-engagement); the one-click unsubscribe link is keyed on
    ``unsubscribe_token``.

    RLS note: this table does NOT have its own RLS policy.  ``creator_id`` is the
    primary key, so a single-row-per-creator read/write never needs RLS to prevent
    cross-tenant leaks — the application always queries by ``creator_id`` directly.
    """

    __tablename__ = "notification_preferences"

    creator_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("creators.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # Legally always-on for true transactional mail (CAN-SPAM / GDPR Art. 6(1)(b)).
    # UI shows the toggle but locks it to True; server-side enforcement is in the
    # send_notification task which treats this as immutable.
    email_transactional: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=True, server_default=sa.text("true")
    )
    # Welcome / first-clip nudge / re-engagement (lifecycle / commercial-leaning).
    # Unsubscribable via one-click link; must be honoured ≤10 business days (CAN-SPAM).
    email_lifecycle: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=True, server_default=sa.text("true")
    )
    inapp_enabled: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=True, server_default=sa.text("true")
    )
    # Web push deferred to Phase 3 (Issue 243 / research/findings/11_notifications_…).
    push_enabled: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.text("false")
    )
    # UUID4 token for no-auth one-click unsubscribe GET /unsubscribe/{token}.
    # Unique so a token cannot be guessed from another creator's token.
    unsubscribe_token: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, nullable=False, unique=True, default=uuid.uuid4
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class NotificationDelivery(Base):
    """Idempotency ledger for every notification send attempt (Issue 243).

    The ``dedupe_key`` UNIQUE constraint (SHA-256 of creator_id:event_type:entity_id)
    is the primary deduplication mechanism — a Celery redelivery gets an
    ``IntegrityError`` on the INSERT and short-circuits without a second send.
    The ``provider_message_id`` column stores the Resend message id returned on
    success so deliverability issues can be diagnosed without logging PII.

    No RLS policy: reads are always by ``creator_id`` in the application layer,
    and this is an internal audit table not exposed to the creator-facing API.
    """

    __tablename__ = "notification_deliveries"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    creator_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("creators.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    entity_id: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    channel: Mapped[NotificationChannel] = mapped_column(
        sa.Enum(NotificationChannel, name="notification_channel_enum"),
        nullable=False,
    )
    # sha256(creator_id:event_type:entity_id) — see notify/dedupe.py.
    # UNIQUE enforces one delivery per (creator, event, entity) triple.
    dedupe_key: Mapped[str] = mapped_column(sa.String(64), nullable=False, unique=True)
    # Resend message id returned on success (no PII — provider-side opaque id).
    provider_message_id: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)
    status: Mapped[NotificationDeliveryStatus] = mapped_column(
        sa.Enum(NotificationDeliveryStatus, name="notification_delivery_status_enum"),
        nullable=False,
        default=NotificationDeliveryStatus.sent,
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )


class Notification(Base):
    """Durable in-app notification row (Issue 243, Issue 81).

    Distinct from the ephemeral per-task Redis Stream (1-hour TTL, requires
    an open connection) and from the operator-only event_logs table (no RLS,
    PII-redacted, not creator-facing).  This table is the creator-visible
    "notification center" — polled on page load, dismissed by the creator.

    RLS policy: ``tenant_isolation`` (ENABLE + FORCE) mirrors chat_conversations
    so creator A can never read creator B's notifications via the app role.
    Every app-layer query additionally filters by ``creator_id`` as a defence-in-
    depth complement to RLS (same pattern as chat_conversations / clips).
    """

    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    creator_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("creators.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Short classifier string, e.g. "clips_ready", "dna_built", "trial_ending".
    kind: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(sa.String(256), nullable=False)
    body: Mapped[str] = mapped_column(sa.Text, nullable=False)
    # Optional deep-link to the relevant page (e.g. /app/review for clips_ready).
    link_url: Mapped[str | None] = mapped_column(sa.String(512), nullable=True)
    # NULL = unread; set on first display in the notification center.
    seen_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    # NULL = not dismissed; set when the creator explicitly dismisses the row.
    dismissed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
