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
from sqlalchemy.ext.asyncio import AsyncSession
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


class SummaryStatus(enum.Enum):
    """Lifecycle of a stream-VOD recap Summary (Issue 190).

    ``pending`` — row created; segment selection not yet run/persisted.
    ``ready``   — segments selected and stored; render tracked by render_status.
    ``failed``  — selection failed permanently (e.g. no usable candidates).
    """

    pending = "pending"
    ready = "ready"
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


class VideoSentiment(enum.Enum):
    """Video-level style-review valence (Issue 370). A deliberate 2-value enum —
    skip/trim/format are clip mechanics that make no sense at video level."""

    like = "like"
    dislike = "dislike"


class InsightType(enum.Enum):
    performer_analysis = "performer_analysis"
    trend = "trend"
    recommendation = "recommendation"


class ClipTriage(enum.Enum):
    """The creator's CURRENT verdict on a clip (Issue 444) — a mutable,
    reversible workflow state, deliberately not an append-only event.

    A native PG enum, matching every other status column in this schema. The
    usual argument for VARCHAR+CHECK — that `ALTER TYPE … ADD VALUE` cannot run
    inside a transaction — has been false since PostgreSQL 12, and this project
    targets PG16. What remains true is that a value can never be *removed* or
    *renamed* cleanly; that risk is bounded here because the taxonomy is a fixed
    three piles and the export finish line is modelled on its own column rather
    than as extra triage values.
    """

    pending = "pending"  # not yet judged — the default pile, i.e. the review queue
    kept = "kept"
    dropped = "dropped"


# Which triage state a feedback action implies, for the one place a rating and a
# verdict are recorded together. `skip` is absent on purpose: skipping is not a
# verdict, so the clip stays in the queue. `format` is absent too — choosing an
# aspect ratio is render mechanics, not a judgement, so it must not silently
# promote a clip out of the queue. Mirrors the 0057 backfill.
TRIAGE_BY_FEEDBACK_ACTION: dict[FeedbackAction, ClipTriage] = {
    FeedbackAction.upvote: ClipTriage.kept,
    FeedbackAction.trim: ClipTriage.kept,
    FeedbackAction.downvote: ClipTriage.dropped,
}


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
    # Poster frame — one 640px JPEG still (Issue 387, migration 0050). Unlike
    # `audio_uri` this DELIBERATELY OUTLIVES the 72h source purge: the WAV is a
    # complete lossless substitute for the source's audio track, whereas a single
    # lossy still reconstructs nothing and functions as an index entry, like
    # `title` and `duration_s`. See docs/COMPLIANCE.md. NULL = no poster.
    poster_uri: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    # Waveform peaks — BBC audiowaveform JSON, ~31 min/max pairs per second
    # (Issue 392, migration 0051). Computed at ingest from `audio_uri`'s WAV, so
    # a video whose audio was already purged can NEVER get peaks: the backfill
    # only reaches videos still inside SOURCE_MEDIA_RETENTION_HOURS. Like
    # `poster_uri` and unlike `audio_uri` the artifact itself OUTLIVES the purge —
    # an 8-bit amplitude envelope is strictly coarser than duration plus a
    # loudness curve and reconstructs no speech or music. See docs/COMPLIANCE.md.
    # NULL = no peaks; the UI draws a labelled flat track, never fake amplitude.
    peaks_uri: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    # Camera region resolved ONCE for the whole video (Issue 439, migration 0056):
    # {version, x, y, width, height, frame:{width,height}, sample_frames}. Clips of
    # one source used to detect this independently and disagree — one clip absorbed
    # the source's SUBSCRIBE/socials overlay and shipped it burned in. A cache of a
    # deterministic measurement, never creator-authored, so the backfill task may
    # recompute it freely. NULL = unresolved (older video, detection declined, or
    # the flag was off at ingest) → the render path falls back to per-clip detection.
    camera_region_jsonb: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Issue 448 — when a transient overlay (a livestream superchat) sits INSIDE
    # the camera region, the region crop cannot exclude it, so the render masks
    # it instead. Separate from `camera_region_jsonb` on purpose: that column is
    # a nine-window median kept on a strict majority and is deliberately blind
    # to transients (Issue 443); this one records exactly those transients.
    # NULL = no masking, which is the pre-448 behaviour.
    overlay_spans_jsonb: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
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
    # Soft-delete marker (Issue 444 schema, Issue 446 behaviour). NULL = active.
    # A timestamp rather than a boolean so restore and audit both know WHEN.
    # Archiving hides the video and frees its media but deliberately PRESERVES
    # the clips and their clip_feedback rows — those are the preference model's
    # training labels, and losing them would silently degrade personalization.
    # This is NOT an erasure mechanism: an archived row still holds creator data,
    # so DELETE /auth/me remains the right-to-erasure path.
    archived_at: Mapped[datetime | None] = mapped_column(
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


class VideoContext(Base):
    """1:1 whole-video LLM context analysis (Issue 415, migration 0053).

    Written by the ``analyze_video_context`` chain member between transcribe and
    build_signals. ``context_jsonb`` holds the validated payload::

        {"version": 1, "summary": ..., "structure": [{start_s, end_s, label}],
         "narrative_arcs": [...], "tone": ..., "audience_relevance": ...,
         "moments": [{start_s, end_s, reason, principle, confidence}]}

    ``moments`` are the LLM-proposed clip candidates (≤ LLM_CANDIDATES_MAX,
    bounds-clamped, principle ∈ the 12 named principles) consumed by the hybrid
    candidate merge (Issue 416). A separate table (not a column on the hot
    ``videos`` row) because most reads of ``videos`` never need this payload.
    The PK doubles as the check-then-insert idempotency key: a Celery
    redelivery sees the existing row and never re-spends the LLM call.
    """

    __tablename__ = "video_context"

    video_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True
    )
    context_jsonb: Mapped[dict] = mapped_column(JSONB, nullable=False)
    model: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    prompt_version: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )


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
        captions_enabled : bool
        zoom_on_peak     : bool
        denoise          : bool
        aspect           : str | None   — "9:16" | "1:1" | "16:9"
        caption_position : str | None   — "top" | "middle" | "bottom" (Issue 427)
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
    __table_args__ = (
        # Backstop for persist_ranked_clips' check-then-insert (Issue 361): the
        # loser of a concurrent double-generation hits this instead of
        # double-inserting the clip set. DEFERRABLE INITIALLY DEFERRED because
        # rerank_with_preference permutes rank values via per-row UPDATEs — an
        # immediate check would raise on the transient swap; deferred, it runs
        # at COMMIT and still catches the race. Migration 0046. NULL ranks are
        # distinct, so unranked rows never conflict.
        sa.UniqueConstraint(
            "video_id",
            "rank",
            name="uq_clips_video_rank",
            deferrable=True,
            initially="DEFERRED",
        ),
    )

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
    # Score semantics (Issue 465, migration 0059):
    # `score` is the IMMUTABLE DNA/LLM fit composite, set once at persist time
    # and never rewritten — every fit reader (recap candidates, proof-of-lift,
    # chat tools, efficacy's dna_composite) depends on that stability.
    # `blended_score` is (1-w)*score + w*pref, written by rerank_with_preference;
    # NULL = personalization never applied (below threshold, no trained model,
    # or the append path, which skips the rerank by design).
    score: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    blended_score: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
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
    # Effective render geometry (Issue 470, migration 0061). The trim/clean
    # confirm swap replaces the delivered video, but `start_s`/`end_s`/
    # `setup_start_s` keep describing the SOURCE window — so every duration,
    # transcript, and caption reader lied about the delivered artifact. These
    # two columns record what was actually delivered. Shape (both):
    #   {"version": 1, "keep_segments_s": [[a, b], ...], "duration_s": <sum>}
    # (see clip_engine/edits.py geometry helpers).
    #
    # `pending_geometry_jsonb` describes the artifact in `cleaned_render_uri`,
    # with keep segments relative to the timeline of the render it was cut FROM
    # (the current `render_uri`). Written by the worker in the SAME commit as
    # `cleaned_render_uri` — the cut list otherwise never exists outside the
    # task (`_clean_clip_async` computes it in-memory) and the edit document is
    # cleared at confirm (Issue 391), so this is the only durable record.
    # Cleared in lockstep with `cleaned_render_uri` (confirm CAS + discard CAS).
    pending_geometry_jsonb: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # `effective_geometry_jsonb` describes the LIVE `render_uri`, with keep
    # segments in ORIGINAL clip-relative seconds (origin = setup_start_s ??
    # start_s) so transcript words map straight through it. Written at
    # /clean/confirm inside the Issue-468 CAS transaction (composed with the
    # prior value for second trims); cleared by the Issue-353 re-render reset,
    # which regenerates the full window from source. NULL = the delivered
    # video IS the original window.
    effective_geometry_jsonb: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Poster frame for the RENDERED deliverable (Issue 387) — reframed, captions
    # burned in, correct aspect. Extracted from the clip the creator will
    # actually publish, which is the most useful thing to show them. NULL until
    # the clip renders, or if extraction failed.
    poster_uri: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    render_status: Mapped[RenderStatus] = mapped_column(
        sa.Enum(RenderStatus, name="render_status_enum"),
        nullable=False,
        default=RenderStatus.pending,
    )
    rank: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    # The creator's CURRENT verdict — a mutable, reversible workflow state, not a
    # training event (Issue 444, migration 0057). `pending` = still in the review
    # queue; `kept`/`dropped` = triaged, and freely moved back and forth.
    #
    # Deliberately separate from `clip_feedback`: that log is append-only and
    # training reads only the LATEST verdict per clip (Issue 444's partition).
    # As shipped: PUT /clips/{id}/triage records the derived verdict row in the
    # same transaction and enqueues the debounced retrain; moving a clip back to
    # `pending` is the retraction path. POST /clips/{id}/feedback carries the
    # richer verdict (tags/notes); `skip` there is an acknowledged no-op, never
    # a verdict (Issue 472).
    #
    # `server_default` is load-bearing, not decoration: during a rolling restart
    # the PREVIOUS image's persist_ranked_clips INSERT does not name this column,
    # so without a database-side default that INSERT would violate NOT NULL and
    # clip generation would 500 for the whole restart window.
    triage: Mapped[ClipTriage] = mapped_column(
        sa.Enum(ClipTriage, name="clip_triage_enum"),
        nullable=False,
        default=ClipTriage.pending,
        server_default=ClipTriage.pending.value,
    )
    # Render style chosen by the creator in the review UI (Issue 119). JSONB
    # mirror of RenderStyleIn (routers/clips.py): subtitle, captions_enabled,
    # zoom_on_peak, denoise, aspect, caption_position. Keys from removed styles
    # (e.g. the Issue-442 "background", whose docs once promised a phantom
    # "brand" value) may persist on old rows and are ignored by the renderer.
    style_preset: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Creator-approved publish metadata (migration 0047). NULL = no applied
    # value: publish_to_youtube falls back to video.title / "#Shorts". Set and
    # cleared via PATCH /clips/{id}; validated there against the YouTube
    # videos.insert limits (title ≤100 chars, description ≤5000 UTF-8 bytes,
    # no angle brackets) so the worker never has to truncate an applied value.
    applied_title: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    applied_description: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    # Pipeline-suggested publish metadata (Issue 417, migration 0054). Written
    # ONCE by the batched generate_clip_metadata task (idempotency filter:
    # suggested_title IS NULL — redelivery fills gaps only), pre-clamped to the
    # YouTube limits. ``applied_*`` stays creator-typed only; publish falls back
    # applied_* → suggested_* → (video.title | "#Shorts"). NULL = generation
    # skipped/failed — clips stay fully usable.
    suggested_title: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    suggested_description: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    suggested_hook: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    suggestions_generated_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    # Speaker-aware crop track (migration 0055, Issue 421) — the UNIFIED wire
    # contract dict computed by clip_engine.reframe.compute_dynamic_crop.
    # NULL = no track (reframe flag off at render time / pre-feature clip);
    # recomputed and replaced (or nulled) in the same done-marking transaction
    # as render_uri on EVERY render — never stale. Served at
    # GET /clips/{id}/crop-track; the list surface only carries the boolean
    # has_crop_track (a track is ~15–20 KB). NOT part of ClipEditDocument —
    # worker writes there would bump CAS revisions and kill client autosaves.
    reframe_track_jsonb: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Download record (Issue 447, migration 0060) — semantics: download initiated
    # (302 issued with disposition=attachment) for the CURRENT render; cleared on
    # re-render (the Issue-353 reset in POST /clips/{id}/render) and on
    # /clean/confirm's artifact swap, both of which replace the render the stamp
    # described. The same endpoint backs the in-app player with
    # disposition=inline, which never stamps — watching is not downloading.
    # NULL = the current render was never downloaded.
    downloaded_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
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


class ClipEditDocument(Base):
    """One row per edited clip: the server-authoritative edit document (Issue 391).

    Replaces ``localStorage['clip:{clipId}:cuts']`` as the source of truth for a
    creator's in-progress edit, so the work survives a cleared cache, a second
    browser and a different machine.

    ``doc`` is the whole document, always read and written as a unit::

        {"version": 1,
         "cuts": [{"id": "…", "start_s": 12.40, "end_s": 15.08}],
         "last_applied_at": null}

    Times are clip-relative seconds. ``indices`` (the transcript word span) is
    DERIVED and deliberately never persisted: it is a pure function of (times,
    transcript), and the transcript is server-owned and mutable, so storing it
    would create a second source of truth that goes stale silently and can index
    past the end of ``words``. It is recomputed on load.

    ``revision`` is a COLUMN rather than a key inside ``doc`` because it
    participates in the WHERE clause of the compare-and-set upsert in
    ``routers/clips.py`` — that is what makes two tabs autosaving concurrently
    safe. ``doc["version"]`` is the unrelated schema version; a reader rejects
    ``version`` greater than it supports rather than half-parsing a newer
    document written by another tab.

    ``MutableDict.as_mutable`` follows ``CreatorStyle`` so in-place mutation is
    tracked by the unit of work without a re-assign.
    """

    __tablename__ = "clip_edit_documents"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    clip_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("clips.id", ondelete="CASCADE"), nullable=False
    )
    # Denormalised from `clips` so the RLS policy is a direct-column comparison
    # rather than a subquery through the parent — the house pattern.
    creator_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("creators.id", ondelete="CASCADE"), nullable=False
    )
    doc: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSONB()),
        nullable=False,
        default=dict,
        server_default=sa.text("'{}'::jsonb"),
    )
    revision: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default=sa.text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    __table_args__ = (
        sa.UniqueConstraint("clip_id", name="uq_clip_edit_documents_clip_id"),
        # Postgres does not auto-index FK columns, and an unindexed FK makes the
        # parent DELETE seq-scan this table. `clip_id` is covered by the unique
        # constraint above; `creator_id` needs its own so right-to-erasure
        # (DELETE FROM creators, which cascades here) stays cheap.
        sa.Index("ix_clip_edit_documents_creator", "creator_id"),
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


class VideoFeedback(Base):
    """Video-level style review (Issue 370, migration 0048).

    What the creator likes/dislikes about a whole video's STYLE — tags mirror
    the ClipFeedback taxonomy shape (valence → tags → note) so the style
    distiller (Issue 371) consumes uniform triples from both tables. At least
    one of tags/note is required at the endpoint (bare valence carries no
    style signal).
    """

    __tablename__ = "video_feedback"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    creator_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("creators.id", ondelete="CASCADE"), nullable=False
    )
    video_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
    )
    sentiment: Mapped[VideoSentiment] = mapped_column(
        sa.Enum(VideoSentiment, name="video_sentiment_enum"), nullable=False
    )
    feedback_tags: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    feedback_note: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )


class CreatorStyleNotes(Base):
    """Distilled style preferences learned from review feedback (Issue 371).

    Single row per creator (like ``creator_style``) holding the LLM-distilled
    "STYLE PREFERENCES" text produced from clip-level feedback tags/notes and
    video-level style reviews. Injected into clip scoring as a third system
    block AFTER the cached DNA block, and into DNA-brief rebuilds via the user
    turn. ``last_input_at`` is the distillation watermark (newest feedback row
    consumed); the debounce in ``distill_style_prefs`` counts rows after it.
    """

    __tablename__ = "creator_style_notes"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    creator_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("creators.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    notes_text: Mapped[str] = mapped_column(sa.Text, nullable=False)
    # How many feedback rows (clip + video) fed the current distillation —
    # surfaced honestly to the creator ("based on N notes").
    source_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    last_input_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )


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


class Summary(Base):
    """Stream-VOD recap artifact spanning many (start,end) segments (Issue 190).

    A dedicated table rather than overloading ``clips``: a montage's many
    segments do not fit a single start_s/end_s row. ``segments`` is a JSONB
    list; each element carries the exact shape the recap renderer (Issue 191)
    consumes verbatim::

        {"start_s": float, "end_s": float, "score": float,
         "principle": str, "rationale": str}

    ``principle`` is an exact named principle from docs/CLIPPING_PRINCIPLES.md
    (same contract as clips). ``dna_version`` records which DNA the selection
    was scored against. Per-creator isolation via the ``tenant_isolation`` RLS
    policy on ``creator_id`` (migration 0041); FK cascade purges rows on
    account deletion (right-to-erasure).
    """

    __tablename__ = "summaries"
    __table_args__ = (
        # At most ONE in-flight recap per video (Issue 361): backstops
        # create_summary's check-then-insert so a double-click cannot enqueue
        # two render_summary jobs. Partial — done/failed rows leave the index,
        # allowing a later re-render. Migration 0046.
        sa.Index(
            "uq_summaries_active",
            "video_id",
            unique=True,
            postgresql_where=sa.text("render_status IN ('pending', 'running')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    creator_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("creators.id", ondelete="CASCADE"), nullable=False
    )
    video_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
    )
    target_duration_s: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    segments: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sa.text("'[]'::jsonb")
    )
    dna_version: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    render_uri: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    render_status: Mapped[RenderStatus] = mapped_column(
        sa.Enum(RenderStatus, name="render_status_enum"),
        nullable=False,
        default=RenderStatus.pending,
    )
    status: Mapped[SummaryStatus] = mapped_column(
        sa.Enum(SummaryStatus, name="summary_status_enum"),
        nullable=False,
        default=SummaryStatus.pending,
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
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
    # Per-retrain offline eval (Issue 202): {"ndcg_at_5","map_at_5","n_eval","computed_at"}.
    # Best-effort — NULL when the creator lacks enough held-out labels to evaluate.
    metrics_jsonb: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
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


class FeatureFlag(Base):
    """Runtime kill switches / feature flags (Issue 284).

    One row per flag key (e.g. ``llm_generation``). A row OVERRIDES the env
    default from config (``FLAG_<KEY>_ENABLED``); a missing row falls back to
    that env default. Written ONLY via ``flags.set_flag()`` so every flip is
    audited (``flag_flipped`` event with actor + reason). No RLS: this is a
    global operations table, not tenant data — it carries no creator ids, no
    YouTube-origin data, and no PII.
    """

    __tablename__ = "feature_flags"

    key: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(sa.Boolean, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    # Operator identity (e.g. shell user running scripts/flags.py) — audit trail.
    updated_by: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    reason: Mapped[str | None] = mapped_column(sa.String(500), nullable=True)


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
    session: AsyncSession,
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
    """Terminal or intermediate state of a single delivery attempt.

    ``pending`` (Issue 530) is the only non-terminal state: the row exists (the
    dedupe claim is committed, freeing the DB connection before the blocking
    provider call — Issue 349) but the send has not yet returned. ``sent`` may
    only be written after the provider call succeeds; before Issue 530 the row
    was committed ``sent`` up front, so a worker killed mid-send left a
    permanently latched false ``sent``.
    """

    pending = "pending"
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
    # Issue 525: WHICH backend handled this row. Without it, a row written while
    # NOTIFY_BACKEND=console is indistinguishable from a real delivery — status is
    # 'sent' either way, and _send_console only logs. Prod carried 17 such rows,
    # every one claiming a delivery that never left the box.
    #
    # Nullable with no backfill, by decision: the pre-existing rows stay as the
    # honest historical artifact of the console era. NULL here means "written
    # before this column existed", which is exactly what it is. Re-sending them is
    # deliberately NOT attempted — they are weeks old, and firing "your clips are
    # ready" for a three-week-old video is worse than silence.
    handled_by: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
    status: Mapped[NotificationDeliveryStatus] = mapped_column(
        sa.Enum(NotificationDeliveryStatus, name="notification_delivery_status_enum"),
        nullable=False,
        # Honest default (Issue 530): a freshly inserted row has not been
        # delivered yet. `sent` is only written after the provider call returns.
        default=NotificationDeliveryStatus.pending,
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
