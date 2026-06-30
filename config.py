import logging
import sys
from pathlib import Path

from pydantic import ValidationError, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _normalize_async_pg_dsn(dsn: str | None) -> str | None:
    """Upgrade a bare libpq Postgres DSN to the psycopg3 async driver scheme.

    SQLAlchemy's async engine (db.py) needs the ``postgresql+psycopg://`` scheme to
    select the psycopg3 async driver. Managed Postgres providers (e.g. Render's
    ``fromDatabase`` connectionString) inject a plain ``postgresql://`` (or the
    legacy ``postgres://``) DSN, which SQLAlchemy maps to the *sync* psycopg2
    driver and then fails when create_async_engine() is called. Rewriting the
    scheme here makes those injected DSNs work with zero call-site changes.
    Already-qualified schemes (``postgresql+psycopg``, ``postgresql+asyncpg``, …)
    and non-Postgres / empty values are passed through untouched. (Render beta)
    """
    if not dsn:
        return dsn
    if dsn.startswith("postgresql+") or dsn.startswith("postgres+"):
        return dsn
    if dsn.startswith("postgresql://"):
        return "postgresql+psycopg://" + dsn[len("postgresql://") :]
    if dsn.startswith("postgres://"):
        return "postgresql+psycopg://" + dsn[len("postgres://") :]
    return dsn


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Required ---
    ANTHROPIC_API_KEY: str
    DATABASE_URL: str
    REDIS_URL: str

    # --- Postgres RLS (Issue 79) ---
    # Connection string for the migration / admin role. Must point to a role
    # with BYPASSRLS (used by Alembic migrations and Celery worker tasks that
    # operate cross-tenant). Falls back to DATABASE_URL when unset, which is
    # the dev-friendly default — single Postgres role with implicit bypass via
    # being the table owner. In production these MUST be different roles; see
    # `docs/DEPLOYMENT.md` for the one-time role-setup runbook.
    DATABASE_MIGRATION_URL: str | None = None

    @property
    def database_migration_url(self) -> str:
        return self.DATABASE_MIGRATION_URL or self.DATABASE_URL

    # --- Beta event logging (Issue 151) ---
    # Dedicated connection string for the high-volume event/telemetry log
    # (event_logs table). Defaults to DATABASE_URL so beta runs on one Postgres,
    # but can point at a separate logical/physical database to keep click/request
    # telemetry off the primary OLTP path. If split out, the 0025 migration must
    # also be applied there. Toggle off to disable DB persistence entirely
    # (events still hit the rotating app.log via observability.log_event).
    LOGS_DATABASE_URL: str | None = None
    EVENT_LOG_DB_ENABLED: bool = True

    @property
    def logs_database_url(self) -> str:
        return self.LOGS_DATABASE_URL or self.DATABASE_URL

    @field_validator(
        "DATABASE_URL",
        "DATABASE_MIGRATION_URL",
        "LOGS_DATABASE_URL",
        mode="after",
    )
    @classmethod
    def _normalize_pg_dsn_scheme(cls, value: str | None) -> str | None:
        # Render (and other managed Postgres) inject a bare postgresql:// DSN;
        # the async engine requires postgresql+psycopg://. Normalize once at load.
        return _normalize_async_pg_dsn(value)

    GOOGLE_OAUTH_CLIENT_ID: str
    GOOGLE_OAUTH_CLIENT_SECRET: str
    OAUTH_REDIRECT_URI: str
    TOKEN_ENCRYPTION_KEY: str
    JWT_SECRET_KEY: str
    ALLOWED_ORIGINS: str

    # --- Key rotation ---
    # Set to the old TOKEN_ENCRYPTION_KEY during rotation to allow decryption of
    # tokens encrypted under the previous key. MultiFernet tries the primary key
    # first; previous key is only used as a fallback. Remove after re-encryption is
    # complete (run scripts/rotate_token_key.py, then clear this variable).
    TOKEN_ENCRYPTION_KEY_PREVIOUS: str | None = None

    # --- Optional with defaults ---
    VOYAGE_API_KEY: str = ""
    JWT_EXPIRY_MINUTES: int = 60
    # Anthropic model + tool versions live here (single source of truth) rather
    # than hardcoded at call sites — see docs/SKILL_FRESHNESS.md. These are
    # perishable: verify against the live Anthropic model/tool catalog
    # (via the /claude-api skill) before each launch.
    ANTHROPIC_MODEL: str = "claude-sonnet-4-6"
    # Per-task model routing (Issue 318). Each key maps one LLM task to its own
    # model so cheap classify-style calls (hooks, chapters, performer analysis)
    # route to Haiku 4.5 while reasoning-heavy or streaming tasks stay on Sonnet
    # 4.6. Use bare aliases (no date suffix) per Anthropic docs. Override any
    # individual key via env var without touching the others. Defaults:
    #   Sonnet 4.6 — scoring, dna_brief, analysis, titles, thumbnails, chat, intake, improvement
    #   Haiku 4.5  — hooks, chapters, performer (cheap classify calls)
    # Source: https://platform.claude.com/docs/en/about-claude/models/overview (2026-06-26)
    ANTHROPIC_MODEL_SCORING: str = "claude-sonnet-4-6"
    ANTHROPIC_MODEL_DNA_BRIEF: str = "claude-sonnet-4-6"
    ANTHROPIC_MODEL_ANALYSIS: str = "claude-sonnet-4-6"
    ANTHROPIC_MODEL_TITLES: str = "claude-sonnet-4-6"
    ANTHROPIC_MODEL_THUMBNAILS: str = "claude-sonnet-4-6"
    ANTHROPIC_MODEL_HOOKS: str = "claude-haiku-4-5"
    ANTHROPIC_MODEL_CHAPTERS: str = "claude-haiku-4-5"
    ANTHROPIC_MODEL_PERFORMER: str = "claude-haiku-4-5"
    ANTHROPIC_MODEL_CHAT: str = "claude-sonnet-4-6"
    ANTHROPIC_MODEL_INTAKE: str = "claude-sonnet-4-6"
    ANTHROPIC_MODEL_IMPROVEMENT: str = "claude-sonnet-4-6"
    # Per-clip LLM feature models (Issues 322–325).
    # All three are Sonnet 4.6 reasoning tasks (DNA-grounded, per-clip context).
    ANTHROPIC_MODEL_CLIP_TITLES: str = "claude-sonnet-4-6"
    ANTHROPIC_MODEL_CLIP_CAPTIONS: str = "claude-sonnet-4-6"
    ANTHROPIC_MODEL_CLIP_EXPLAIN: str = "claude-sonnet-4-6"
    # web_search_20260209 is the GA version with dynamic filtering: Claude
    # writes code to pre-filter search results before they reach the context
    # window, reducing tokens read and improving accuracy. Same tool API
    # shape as the prior _20250305 (`name: "web_search"`); no call-site
    # changes required to swap. (Issue 84)
    ANTHROPIC_WEB_SEARCH_TOOL: str = "web_search_20260209"
    # LLM cost constants (USD per million tokens, standard tier).
    # Source: Anthropic pricing page (platform.claude.com, fetched 2026-06-23).
    # Override via env var to pick up price changes without a code deploy.
    # (Issue 220 — Usage cost ledger)
    COST_PER_MTOK_IN_SONNET: float = 3.0  # Sonnet 4.6 input: $3/MTok standard
    COST_PER_MTOK_OUT_SONNET: float = 15.0  # Sonnet 4.6 output: $15/MTok standard
    COST_PER_MTOK_IN_HAIKU: float = 1.0  # Haiku 4.5 input: $1/MTok standard
    COST_PER_MTOK_OUT_HAIKU: float = 5.0  # Haiku 4.5 output: $5/MTok standard
    # Cache-read multiplier: prompt-cache hits are billed at 10% of the base input rate.
    # Source: platform.claude.com/docs/en/about-claude/pricing (fetched 2026-06-23).
    COST_CACHE_READ_MULTIPLIER: float = 0.1
    # Cache-WRITE multiplier: writing a 5-min-TTL ephemeral cache block costs 1.25× the
    # base input rate (1h-TTL is 2×; callers using ttl:"1h" — e.g. clip_engine/scoring.py —
    # pass cache_write_multiplier=2.0 explicitly). Source: same pricing page. (cost ledger)
    COST_CACHE_WRITE_MULTIPLIER: float = 1.25
    # Deepgram Nova-2 pre-recorded transcription cost per minute (pay-as-you-go).
    # Source: deepgram.com/pricing (fetched 2026-06-23).
    COST_PER_MIN_DEEPGRAM: float = 0.0043
    # Voyage AI voyage-3.5 embedding cost per million tokens.
    # Source: docs.voyageai.com/docs/pricing (fetched 2026-06-23).
    COST_PER_MTOK_VOYAGE: float = 0.06
    # Cloudflare R2 standard storage cost per GB per month.
    # Source: developers.cloudflare.com/r2/pricing (fetched 2026-06-23).
    COST_PER_GB_MO_R2: float = 0.015
    # Cloudflare R2 Class A operations (PUT/DELETE) cost per million operations.
    # Source: developers.cloudflare.com/r2/pricing (fetched 2026-06-23).
    COST_PER_M_R2_CLASS_A: float = 4.50
    # Cloudflare R2 Class B operations (GET/HEAD) cost per million operations.
    # Source: developers.cloudflare.com/r2/pricing (fetched 2026-06-23).
    COST_PER_M_R2_CLASS_B: float = 0.36
    # Estimated cost per CPU-second for ffmpeg render on Kubernetes node.
    # Based on K8s node cost estimate; tune from real billing data once GKE staging runs.
    # (Issue 275 — K8s staging cluster is the linchpin for empirical validation)
    COST_PER_RENDER_CPU_S: float = 0.000025
    # Version stamp for the price book. Update this string whenever any rate changes —
    # a version mismatch between a stored cost_estimate and this stamp signals a
    # rate-change event (FinOps Foundation cost-per-unit standard; finops.org/framework/phases/).
    PRICE_BOOK_VERSION: str = "2026-06-23"
    # --- Pro chatbot (Issue 152) ---
    # Per-creator daily message cap — the load-bearing margin guard. Bounds
    # worst-case spend to ≈ CHAT_DAILY_MESSAGE_LIMIT × ~$0.04/heavy message per
    # active creator/day. Tune from real token logs (DECISIONS 2026-06-17).
    CHAT_DAILY_MESSAGE_LIMIT: int = 25
    # --- Per-creator pre-job quota (Issue 228) ---
    # Daily ceiling on LLM-backed jobs (titles/thumbnails/insights/improvement/
    # analysis/generate_clips) per creator. Stacked beneath the existing hourly
    # burst limits as a slowapi "/day" cap. Bounds worst-case Anthropic/Deepgram
    # spend per creator/day. Starting point — tune from real token logs.
    LLM_DAILY_JOB_LIMIT: int = 50
    # Separate per-creator daily ceiling specifically for brief-generating
    # endpoints (titles, thumbnails, insights analysis, improvement brief).
    # Independently tunable from LLM_DAILY_JOB_LIMIT so operators can control
    # the most expensive single-request inference paths without touching the
    # shared job queue cap.
    BRIEF_DAILY_LIMIT_PER_CREATOR: int = 50
    # Daily ceiling on render jobs (render_clip/clean_clip/submit_cuts/
    # ingest_clip) per creator. Bounds ffmpeg CPU + Cloudflare R2 egress per
    # creator/day. Starting point — tune from real billing data.
    RENDER_DAILY_JOB_LIMIT: int = 60
    # Max creator-scoped tool rounds per message before the model is forced to
    # answer in text. Caps agentic token blow-up (tools burn 3–30× a plain turn).
    CHAT_MAX_TOOL_ITERATIONS: int = 4
    # Hard per-reply output ceiling.
    CHAT_MAX_TOKENS: int = 1500
    # How many prior turns (user+assistant pairs) of history to send per request.
    CHAT_HISTORY_TURNS: int = 8
    TRANSCRIPTION_BACKEND: str = "deepgram"
    # Job-level upper bound for a single transcription (Issue 68). A hung provider
    # fails the task after this many seconds (→ Celery retry) instead of stalling
    # the worker forever.
    TRANSCRIPTION_TIMEOUT_S: int = 300
    # Per-request socket timeout for the hosted backends (Deepgram / AssemblyAI).
    # Keep < TRANSCRIPTION_TIMEOUT_S: a hung provider socket must make the blocking
    # SDK call return — unwinding the leaked worker thread — BEFORE the job-level
    # wait_for gives up (wait_for cannot cancel the OS thread it spawned). (Issue 76)
    TRANSCRIPTION_HTTP_TIMEOUT_S: int = 120
    # Reject an audio file larger than this before sending it to a hosted backend —
    # fail fast with a clear error rather than buffer a pathological file. A normal
    # 16 kHz mono WAV is ~115 MB/hour, so the default allows ~9h. (Issue 76)
    TRANSCRIPTION_MAX_MB: int = 1024
    DEEPGRAM_API_KEY: str = ""
    ASSEMBLYAI_API_KEY: str = ""
    WHISPER_MODEL: str = "large-v3"
    # Hard cap for the ffmpeg audio-extract subprocess. Real extraction is far faster
    # than realtime; this only kills a wedged/hung ffmpeg so it can't tie up a worker
    # slot indefinitely (probe already uses timeout=30). (Issue A / Issue 76)
    FFMPEG_EXTRACT_TIMEOUT_S: int = 1800
    # Cap audio analysis (extract_audio_events) at this many seconds of audio to avoid
    # librosa loading a multi-hour WAV entirely into RAM (OOM vector under concurrent
    # workers). Audio beyond the cap is silently truncated; a WARNING is emitted.
    # Default: 4 hours — covers any realistic YouTube video with headroom. (Issue 334)
    AUDIO_ANALYSIS_MAX_DURATION_S: int = 14400
    # Timeout (seconds) for the ffmpeg showwavespic subprocess in generate_waveform_image.
    # The old hardcoded 60 s was insufficient for very long source files; callers that
    # know the audio duration can scale the timeout further via the duration_s kwarg.
    # (Issue 334)
    WAVEFORM_TIMEOUT_S: int = 300
    STORAGE_BACKEND: str = "local"
    R2_ACCOUNT_ID: str = ""
    R2_ACCESS_KEY_ID: str = ""
    R2_SECRET_ACCESS_KEY: str = ""
    R2_BUCKET: str = ""
    SOURCE_MEDIA_RETENTION_HOURS: int = 72
    # ── Disaster-recovery backups (Issue 256) ─────────────────────────────────
    # Nightly encrypted pg_dump is uploaded to a SEPARATE R2 bucket (3-2-1 rule:
    # a media-bucket mistake or compromised media credential must not be able to
    # touch backups). These settings give the backup tooling a typed, documented
    # home; the actual run is scripts/backup_pg.sh on host cron. They are NOT in
    # _require_prod_secrets on purpose — the API/worker serving traffic must not
    # fail to boot because a cron-only setting drifted; backup_pg.sh validates its
    # own required env at runtime instead (DECISIONS 2026-06-27).
    BACKUP_R2_BUCKET: str = ""
    # age/openssl symmetric passphrase for the dump. NEVER logged, never in argv.
    # The dump carries Fernet *ciphertext* tokens, so it is useless without the
    # separately-escrowed TOKEN_ENCRYPTION_KEY (Issue 255) even if this leaks —
    # and this passphrase must NOT be escrowed inside the backup it protects.
    BACKUP_ENCRYPTION_KEY: str = ""
    # Retention stays <= 30 days for the analytics rows the dump carries, to honor
    # the YouTube 30-day staleness rule (COMPLIANCE.md).
    BACKUP_RETENTION_DAILY: int = 14
    BACKUP_RETENTION_WEEKLY: int = 8
    # Issue 250 — GDPR Art. 5(1)(e) storage-limitation for behavioral telemetry.
    # 90-day rolling window is the industry-standard default for SaaS event logs
    # (common range: 60–180 days). No PII is stored in event_logs (_redact() at
    # ingestion), so the window is defined by analytical utility rather than a
    # legal minimum. Raising above 180 days should be justified in DECISIONS.md.
    EVENT_LOG_RETENTION_DAYS: int = 90
    # Wave-4 Fix 3 (Issue 75b) — YouTube API Services Developer Policies
    # Sections III.E.4.b + III.D.2.3.b require API clients to verify
    # authorization every 30 calendar days OR delete the stored API data.
    # When a creator's analytics rows fail to refresh (token revoked, quota
    # exhausted, etc.) `fetched_at` stops advancing; the daily Beat purge
    # deletes rows past this cutoff. 30 days is the exact number in the
    # policy, the example in Google's compliance guide, and what Google's
    # compliance reviewers check during OAuth app verification. Lengthening
    # past 30 days would be a documented ToS violation; shortening is safe
    # but trades off freshness for no compliance benefit.
    # Source: https://developers.google.com/youtube/terms/developer-policies
    YOUTUBE_ANALYTICS_MAX_STALENESS_DAYS: int = 30
    # ── Clip engine — sentence-boundary snapping (Issue 127) ─────────────────
    # Minimum silence gap (ms) treated as a sentence boundary when no terminal
    # punctuation token is found within max_snap_s of the cut point.
    SENTENCE_BOUNDARY_MIN_PAUSE_MS: int = 400
    # Hard cap on how far (seconds) the engine will walk from a cut point to find
    # the nearest sentence boundary. If nothing is found within this range the
    # original timestamp is kept unchanged — better to hold than to snap too far.
    MAX_SNAP_S: float = 3.0

    # ── Filler-word + silence removal (Issue 134) ──────────────────────────
    # Inter-word gap (ms) above which a silence is removed by the cleaning pass.
    # See docs/DECISIONS.md 2026-06-07 for the 800ms default rationale.
    SILENCE_REMOVAL_THRESHOLD_MS: int = 800
    # Breath left on each side of every silence cut (ms). Sounds natural AND
    # lets the audio waveform taper toward zero so the splice doesn't click.
    SILENCE_TAIL_MS: int = 150
    # Tier-2 fillers ("like", "you know", …) are only excised when flanked by
    # an inter-word gap >= this on at least one side — the published heuristic
    # for separating filler "like" from the verb "like" without POS tagging.
    FILLER_TIER2_FLANK_GAP_MS: int = 150
    # Tier-2 fillers longer than this duration (ms) are presumed deliberate
    # speech rather than disfluency and skipped.
    FILLER_TIER2_MAX_DURATION_MS: int = 600

    # Issue 227 — ingest length clamp for YouTube video titles.
    # YouTube's published title limit is 100 characters (developers.google.com/youtube/v3/docs/videos).
    # 200 chars is a 2× safety margin that only truncates pathological/synthetic inputs — no
    # legitimate YouTube title can exceed 100 chars per the API contract. This prevents
    # adversarially-crafted titles from acting as injection-payload carriers (OWASP LLM01) or
    # creating a token-cost / DoS vector when the title enters the prompt corpus.
    MAX_INGESTED_TITLE_CHARS: int = 200

    # Issue 227 — ingest length clamp for YouTube video descriptions.
    # YouTube's published description limit is 5,000 characters
    # (developers.google.com/youtube/v3/docs/videos — `snippet.description` max: 5000 chars).
    # 10,000 chars is a 2× safety margin that only truncates adversarially-crafted or
    # corrupted inputs. Descriptions are NOT currently stored on the Video model — this cap
    # is defensive/future-proofing: applied at the ingest boundary so that if description
    # ingestion is added later the guard is already in place and cannot be forgotten.
    # The clamp uses the same word-boundary rsplit pattern as titles (clamp_ingest_field).
    MAX_INGESTED_DESC_CHARS: int = 10000
    # ── Per-frame active-speaker reframe (Issue 189) ──────────────────────────
    # Gated off by default: the per-frame MediaPipe + sendcmd path has NEVER
    # been verified on a real render environment (ffmpeg + real multi-speaker
    # media required). The legacy single-keyframe Haar crop in render.py
    # remains the production default until this flag is flipped on staging.
    # Flip to True ONLY after the render-env smoke test passes (Issue 189 AC).
    # See clip_engine/reframe.py and docs/DECISIONS.md (2026-06-23, Issue 189).
    ACTIVE_SPEAKER_REFRAME_ENABLED: bool = False

    # Frames-per-second to sample for face detection in the per-frame reframe
    # path. 5 fps is sufficient for talking-head content and keeps compute
    # proportional to clip duration. Tune up if speaker switches are missed in
    # render-env tests.
    REFRAME_SAMPLE_FPS: float = 5.0

    CLIPS_PER_VIDEO_DEFAULT: int = 8
    # Auto-render generated clips the moment clip generation finishes, so the
    # Review queue is watch-ready with zero manual steps (the upload already
    # consented to — and was charged — the minutes; render adds no extra spend).
    # When False, clips stay pending until the creator triggers a render. (auto-render)
    AUTO_RENDER_CLIPS: bool = True
    # Cap on how many of the ranked clips auto-render per video. 0 = all
    # generated candidates (≤ CLIPS_PER_VIDEO_DEFAULT). Set >0 to render only
    # the top-N highest-fit clips immediately and leave the rest on demand.
    AUTO_RENDER_TOP_N: int = 0
    MIN_VIDEOS_FOR_DNA: int = 10
    MIN_SHORTS_FOR_DNA: int = 5
    # YouTube raised the Shorts maximum to 180s in October 2024
    # (https://support.google.com/youtube/answer/10059070). Any uploaded
    # vertical video at or below this duration is treated as a Short.
    # Configurable so a future spec change is a one-line update. (Issue 87)
    SHORTS_MAX_DURATION_S: int = 180
    # Per-type caps on the DNA candidate set. Queried separately so a prolific Shorts
    # creator can't drown out long-form signal in a single mixed pool. Both sorted
    # by published_at DESC — DNA reflects current style, not historical average.
    # Also bounds Phase 2 of catalog sync to ≤125 YouTube Analytics API calls per
    # first-sync (~4 min), leaving the rest to the hourly Beat task. (Issue 120)
    DNA_LONGS_CAP: int = 50
    DNA_SHORTS_CAP: int = 75
    # ── Style learning (Issue 187) ─────────────────────────────────────────────
    # Minimum number of times a creator must have chosen the same value for a
    # kit field (in the last 20 renders) before the product surfaces a
    # 'make it your default?' suggestion.  Cold-start safe: below this count
    # no suggestion is shown.  5 is the standard smart-default threshold
    # documented in the USPTO 10860981 patent art and NNG default-effect
    # literature.  Lower = noisier; higher = too conservative for small channels.
    STYLE_LEARN_THRESHOLD: int = 5

    PERSONALIZATION_THRESHOLD_LABELS: int = 20
    # Recency-decay half-life (days) for preference-model sample weights. Was a
    # hardcoded ln(2)/30 in preference/decay.py; parameterized (Issue 200) so the
    # eval harness (Issue 198) can grid-search {15,30,60,90} on a held-out NDCG@5
    # split. Default 30 is a reasonable prior, but published domain half-lives span
    # 43–150d — change the default ONLY if a value clears the incumbent's CI. The
    # DNA builder keeps its SEPARATE 90-day half-life; do not unify them. (Issue 200)
    DECAY_HALF_LIFE_DAYS: int = 30
    # Max weight the preference model gets in the rerank blend once mature. Below
    # PERSONALIZATION_THRESHOLD_LABELS the weight is 0 (honest DNA-only fallback);
    # it ramps linearly to this cap by 2× the threshold. (Issue 60)
    PREFERENCE_WEIGHT_CAP: float = 0.5
    # Per-worker LRU cache of deserialized preference scorers, keyed by
    # (creator_id, version). Bounds memory while letting rerank skip the
    # lock-contended joblib load when the model is unchanged. (Issue 78a)
    PREFERENCE_SCORER_CACHE_SIZE: int = 128
    # Newest-first cap on training-feedback rows pulled into a single
    # build_and_save fit. Recency-decay sample weights (30d half-life) make
    # older rows worth ~0, so truncating the long tail is correctness-free.
    # 5000 is the industry-standard ceiling for a per-user LightGBM ranker
    # at 30d half-life (Spotify/Netflix sklearn pipelines). (Issue 102)
    PREFERENCE_MAX_TRAINING_LABELS: int = 5000
    LLM_TIMEOUT_SECONDS: int = 120
    ENV: str = "development"
    YTDLP_ENABLED: bool = False
    YOUTUBE_QUOTA_DAILY_UNITS: int = 8000
    # Per-creator/day sub-budget charged ONLY to the non-interactive Beat
    # analytics-refresh fan-out (Issue 260). Bounds how many units a single
    # creator's refresh can drain so the fan-out can never starve interactive
    # onboarding under the global YOUTUBE_QUOTA_DAILY_UNITS outer cap. Refresh
    # cost is ~10-60 units/creator (catalog list + per-video metadata + reports),
    # so 300 leaves wide headroom while capping pathological fan-out.
    YOUTUBE_QUOTA_PER_CREATOR_REFRESH_UNITS: int = 300
    # TTL (seconds) for the Redis-backed ETag/body cache used for conditional
    # YouTube Data API GETs (Issue 260). A cached 304 (If-None-Match match)
    # returns the stored body and spends NO quota — the measurable quota-unit
    # reduction lever. 6h balances freshness against Beat-refresh cadence.
    YOUTUBE_ETAG_CACHE_TTL_S: int = 21600
    # Privacy status for clips published via the API (Issue 195). Forced "private"
    # until the YouTube API compliance audit clears — the creator flips each Short
    # to public manually. Flip to "public" only post-audit.
    YOUTUBE_PUBLISH_PRIVACY: str = "private"

    # Cache-busting query string appended to every `/static/*.css` and
    # `/static/*.js` reference in served HTML. Set at image-build time from
    # the short git SHA via the GIT_SHA build-arg → STATIC_VERSION env var
    # (see Dockerfile + .github/workflows/docker-publish.yml). Defaults to
    # "dev" locally so the middleware still runs and tests can assert on a
    # deterministic value.
    STATIC_VERSION: str = "dev"

    UPLOAD_MAX_MB: int = 500
    LOCAL_MEDIA_DIR: str = "./media"

    # ── Security headers (Issue 229) ───────────────────────────────────────────
    # Additional CSP source expressions appended to the baseline CSP (Issue 229).
    # E.g. "style-src 'self' https://fonts.googleapis.com;
    # font-src 'self' https://fonts.gstatic.com" for Google Fonts usage.
    # Leave empty for the strict baseline (only 'self' + secure directives).
    CSP_EXTRA_SOURCES: str = ""

    # ── CSRF Fetch-Metadata defence (Issue 230) ────────────────────────────────
    # Enable the Sec-Fetch-Site check on mutating (POST/PUT/PATCH/DELETE) routes.
    # Defaults to False in development/test so TestClient (which does not send
    # Sec-Fetch-* headers) does not produce false-positive 403s. Set to True in
    # production where real browsers always send Sec-Fetch-Site.
    CSRF_FETCH_METADATA_ENABLED: bool = False

    # Celery soft-time-limit as a config value so the transcription-timeout
    # validator below can assert the 30s cleanup-breathing-room invariant
    # without importing celery_app (which would create a circular import).
    # Must stay in sync with task_soft_time_limit in worker/celery_app.py.
    # (Issue 105 — Fix 5)
    CELERY_SOFT_TIME_LIMIT_S: int = 3000

    # RedBeat distributed beat scheduler (Issue 263).
    # Must point to the same Redis as REDIS_URL in dev/staging; in production use
    # the HA Redis URL (Memorystore/Upstash). RedBeat stores the schedule and a
    # distributed lock in Redis (key prefix 'redbeat::'), preventing duplicate task
    # scheduling across beat restarts without multiple-replica coordination.
    # Falls back to REDIS_URL when unset so dev/test need no extra config.
    REDBEAT_REDIS_URL: str | None = None

    @property
    def redbeat_redis_url(self) -> str:
        return self.REDBEAT_REDIS_URL or self.REDIS_URL

    # ── Observability (Issue 75f / Issue 122) ──────────────────────────────────
    # JSON structured logs (one object per line) for log aggregators. Defaults on;
    # set false for human-readable text in local dev.
    LOG_JSON: bool = True
    # Root log level. INFO (default) is the verbose-but-safe operational level: it
    # logs every LLM call's token usage, each outbound HTTP request line (httpx),
    # pipeline stage transitions, and errors with tracebacks — ideal for watching
    # E2E activity. DEBUG is intentionally NOT the default: at DEBUG, httpx logs
    # request HEADERS (which include the Anthropic x-api-key), so DEBUG can leak the
    # key into logs. Only set DEBUG for short, local diagnosis — never standing in prod.
    LOG_LEVEL: str = "INFO"
    # Directory for persistent log files. Defaults to /app/logs which maps to
    # ./logs on the host via the .:/app Docker volume — readable after a session
    # ends without any extra mount. Set to "" to disable file logging.
    LOG_DIR: str = "/app/logs"

    @property
    def log_level_int(self) -> int:
        """Resolve LOG_LEVEL (a name like 'INFO'/'DEBUG') to the logging int.

        Falls back to INFO for an unknown/empty value so a typo never silences logs.
        """
        import logging as _logging

        return _logging.getLevelNamesMapping().get(self.LOG_LEVEL.upper(), _logging.INFO)

    # ── Verbose full-content logging (pre-production debugging) ────────────────
    # When enabled, every load-bearing operation writes a COMPLETE record — raw
    # prompt/response/transcript content, full request bodies, full ffmpeg commands,
    # and full tracebacks — to a dedicated `verbose` logger (<LOG_DIR>/verbose-*.log,
    # or stdout when LOG_DIR=""). This deliberately bypasses the PII/secret redaction
    # that governs the normal logs (docs/COMPLIANCE.md).
    #
    # Default-safe in production: when ENV == "production" it stays OFF unless the
    # operator ALSO sets VERBOSE_LOGGING_ALLOW_PROD=true. That second flag is the
    # explicit, deliberate opt-in for the private beta (which deploys as
    # ENV=production on Render) — it can't be turned on by VERBOSE_LOGGING alone, so
    # a routine deploy never leaks content by accident. Turn BOTH off before public
    # launch. (docs/DECISIONS.md 2026-06-29)
    VERBOSE_LOGGING: bool = False
    # Required IN ADDITION to VERBOSE_LOGGING to enable the verbose sink when
    # ENV=production. No effect off-prod (verbose follows VERBOSE_LOGGING there).
    VERBOSE_LOGGING_ALLOW_PROD: bool = False

    @property
    def verbose_logging_enabled(self) -> bool:
        """Whether the full-content verbose sink is active.

        Off-prod: follows ``VERBOSE_LOGGING`` directly. In production: additionally
        requires ``VERBOSE_LOGGING_ALLOW_PROD`` — raw-content logging is a deliberate
        compliance deviation, so production demands a second explicit opt-in and can
        never be enabled by ``VERBOSE_LOGGING`` (or a copied .env) alone.
        """
        if not self.VERBOSE_LOGGING:
            return False
        if self.ENV == "production":
            return self.VERBOSE_LOGGING_ALLOW_PROD
        return True

    # Inbound header carrying a correlation id from an upstream proxy/gateway. If
    # absent or malformed, the middleware mints a UUID4. Echoed back on the response.
    REQUEST_ID_HEADER: str = "X-Request-ID"
    # Expose Prometheus golden-signal metrics at /metrics. Disable to drop the
    # endpoint entirely (the scrape surface) without touching the rest.
    METRICS_ENABLED: bool = True
    # Bearer token required to scrape /metrics. When set, callers must send
    # `Authorization: Bearer <token>`. Empty = unauthenticated (dev / internal-only
    # network); in production, an empty token auto-disables /metrics (see the validator
    # below) so the scrape surface is never exposed unauthenticated. (Issue 76)
    METRICS_TOKEN: str = ""

    # ── Stripe billing ────────────────────────────────────────────────────────
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    STRIPE_PUBLISHABLE_KEY: str = ""
    APP_BASE_URL: str = "http://localhost:8000"
    FREE_TRIAL_MINUTES: int = 60
    # Issue 126 — trial window (calendar days). Set on first OAuth login; the
    # 402 paywall reads it live + the dashboard banner counts down against it.
    TRIAL_DURATION_DAYS: int = 7
    # Threshold below which the dashboard nav chip lights up amber + pre-action
    # warnings render before Generate / Queue. Tuneable without a deploy.
    LOW_BALANCE_THRESHOLD_MINUTES: int = 10
    # ── Lifecycle email sequence (Issue 246) ─────────────────────────────────
    # First-clip nudge: fires to a creator who signed up at least this many days
    # ago but has never uploaded a video (no Video rows). 3 days is the common
    # SaaS onboarding-nudge window — long enough to not feel pushy, short enough
    # to recover activation before the cohort goes cold.
    LIFECYCLE_NUDGE_AFTER_DAYS: int = 3
    # Re-engagement: fires to a previously-active creator (has ≥1 Video) who has
    # reviewed no clips (no ClipFeedback) within this many days. 14 days is the
    # standard "dormant" cutoff for win-back email in creator SaaS.
    LIFECYCLE_INACTIVITY_DAYS: int = 14
    # Shared frequency cap across ALL lifecycle events (welcome / nudge /
    # re-engagement): at most one lifecycle email per creator per this window.
    # 48h prevents a welcome + nudge landing on the same day.
    LIFECYCLE_FREQUENCY_CAP_HOURS: int = 48
    # Per-request HTTP timeout for the Stripe SDK. Default SDK timeout is ~80s;
    # one stuck call would pin an asyncio.to_thread executor slot for that long.
    # Scale-checklist E (backpressure): every external call needs a bounded
    # timeout. (Issue 106)
    STRIPE_TIMEOUT_S: int = 10
    # Issue 205 — Stripe ledger reconciliation sweep. The daily Beat task looks
    # back this many hours for paid Checkout sessions and grants minutes to any
    # that are missing a MinutePack row. 48h default: covers one missed Beat run
    # plus a Stripe retry window. Stripe keeps events/sessions for 30 days via
    # the API so this can be raised further if needed.
    STRIPE_RECONCILE_LOOKBACK_HOURS: int = 48
    # Issue 207 — Stripe Tax via automatic_tax[enabled]=true.
    # DEFAULT FALSE. Flip to true ONLY after ≥1 active Stripe tax registration
    # has been created in Tax > Registrations (dashboard.stripe.com/tax/registrations).
    # Enabling without a registration causes Stripe to collect $0 tax (documented
    # safe — no error), but the flag should still track the real business decision.
    # Prerequisite: first US sales-tax nexus or equivalent registration is
    # confirmed. See docs/DECISIONS.md (Issue 207) for when to flip.
    # Source: https://docs.stripe.com/tax/checkout/page
    STRIPE_TAX_ENABLED: bool = False

    # ── Clickwrap consent versions (Issue 299) ────────────────────────────────
    # Bump TOS_VERSION or PRIVACY_VERSION (ISO-8601 date) whenever a material
    # change is published to /static/tos.html or /static/privacy.html.  The
    # recorded version string on each Creator row lets a future re-prompt path
    # compare the stored version against the current one and gate the OAuth CTA.
    TOS_VERSION: str = "2026-06-23"
    PRIVACY_VERSION: str = "2026-06-23"

    # ── Error tracking — Sentry / GlitchTip (Issue 281) ───────────────────────
    # Set SENTRY_DSN to a Sentry project DSN or a GlitchTip DSN (identical
    # protocol — only the URL differs). Empty string disables the SDK entirely,
    # which is the correct default in dev/CI. Never log or commit a real DSN.
    SENTRY_DSN: str = ""
    # Short name used as the Sentry "environment" tag (e.g. "staging", "production").
    # Defaults to ENV so no extra config is needed in most cases.
    SENTRY_ENVIRONMENT: str = ""

    @property
    def sentry_environment(self) -> str:
        return self.SENTRY_ENVIRONMENT or self.ENV

    # Git SHA / image tag for Sentry release tracking. Set via IMAGE_SHA env var
    # at container build time (e.g. short git SHA). Empty → not sent.
    IMAGE_SHA: str = ""

    # ── OpenTelemetry / Grafana Cloud (Issue 326) ─────────────────────────────
    # All OTEL_* settings are optional — an empty OTEL_EXPORTER_OTLP_ENDPOINT
    # disables the entire SDK (no imports, no network calls) so dev/CI stays
    # fully offline. Never log OTEL_EXPORTER_OTLP_HEADERS — it carries the
    # Grafana Cloud Basic-auth token.
    #
    # Grafana Cloud OTLP endpoint (e.g.
    #   https://otlp-gateway-prod-us-east-0.grafana.net/otlp).
    # Empty string = OTel completely disabled (default in dev/CI).
    OTEL_EXPORTER_OTLP_ENDPOINT: str = ""
    # OTLP auth headers in the OTel-standard format: "key=value,key2=value2".
    # Grafana Cloud uses: "Authorization=Basic <base64(instanceID:token)>".
    # Treat as a secret — never emit in logs.
    OTEL_EXPORTER_OTLP_HEADERS: str = ""
    # Human-readable service name attached to every span/metric/log.
    # init_otel accepts an explicit service_name arg that overrides this so
    # main.py can use "creatorclip-web" and celery_app.py "creatorclip-worker".
    OTEL_SERVICE_NAME: str = "creatorclip"
    # Head-based trace sampling rate [0.0, 1.0].  1.0 = sample everything
    # (correct default for a ≤100-user beta with tiny volume).
    OTEL_TRACES_SAMPLE_RATE: float = 1.0
    # Push metrics via OTLP to Grafana Cloud.  Keep prometheus-client for local
    # /metrics — this is the push path, not a replacement.
    OTEL_METRICS_ENABLED: bool = True
    # Attach an OTel LoggingHandler and push log records via OTLP.
    # Default OFF — Grafana Cloud Loki is best served via a Render syslog drain
    # (zero code, see docs/RENDER_DEPLOY.md §12).  Enable only if you prefer
    # in-process log shipping over the syslog-drain approach.
    OTEL_LOGS_ENABLED: bool = False

    # ── Transactional email (Issue 242) ────────────────────────────────────────
    # NOTIFY_BACKEND controls where send() dispatches:
    #   'console' — renders + logs; no external call (default in dev / CI)
    #   'resend'  — sends via Resend SDK; RESEND_API_KEY must be set
    NOTIFY_BACKEND: str = "console"
    # Resend API key (https://resend.com — 3k/month free). Required when
    # NOTIFY_BACKEND='resend'. Never log or expose.
    RESEND_API_KEY: str = ""
    # From-address used for all outbound transactional emails. Must match a
    # domain verified in the Resend dashboard (e.g. noreply@autoclip.studio).
    EMAIL_FROM: str = ""
    # Issue 246 — physical postal address rendered in lifecycle (commercial-
    # leaning) emails. CAN-SPAM §A.5 requires a valid physical postal address in
    # every commercial message. OPTIONAL with an EMPTY default so production boot
    # never fails — but it doubles as the lifecycle/welcome SAFETY GATE: while
    # MAILING_ADDRESS is unset, send_notification SKIPS every lifecycle email
    # (welcome / nudge / re-engagement) and only logs the skip. Set this to a real
    # address before enabling lifecycle email in production.
    MAILING_ADDRESS: str = ""

    @field_validator(
        "PERSONALIZATION_THRESHOLD_LABELS",
        "DECAY_HALF_LIFE_DAYS",
    )
    @classmethod
    def _positive_preference_ints(cls, v: int, info: ValidationInfo) -> int:
        """Fail fast on non-positive preference tunables (Issue 338).

        ``DECAY_HALF_LIFE_DAYS`` feeds ``λ = ln(2)/H`` at import in
        ``preference/decay.py`` — H=0 raises ZeroDivisionError at import and H<0
        inverts the decay (older feedback weighted *more*). A non-positive
        personalization threshold breaks the cold-start ramp. Catch both here with
        a clear message instead of a cryptic downstream failure.
        """
        if v <= 0:
            raise ValueError(f"{info.field_name} must be > 0 (got {v})")
        return v

    @field_validator("PREFERENCE_WEIGHT_CAP")
    @classmethod
    def _weight_cap_in_unit_range(cls, v: float) -> float:
        """The preference blend weight must stay in (0, 1] (Issue 338).

        ``score = (1-w)*dna + w*pref`` — a cap outside (0, 1] would over- or
        under-weight personalization and can push blended scores out of range.
        """
        if not (0.0 < v <= 1.0):
            raise ValueError(f"PREFERENCE_WEIGHT_CAP must be in (0, 1] (got {v})")
        return v

    @model_validator(mode="after")
    def _validate_notify_backend(self) -> "Settings":
        """Fail fast when Resend is selected but the API key is absent.

        A missing key would surface only at first send (silently dropped
        or runtime error). Catching it at startup prevents silent activation
        leaks in production where NOTIFY_BACKEND is set but the secret is
        forgotten. (Issue 242)
        """
        if self.NOTIFY_BACKEND == "resend" and not self.RESEND_API_KEY:
            raise ValueError(
                "NOTIFY_BACKEND='resend' requires RESEND_API_KEY to be set. "
                "Generate a key at https://resend.com and add it to .env."
            )
        return self

    @model_validator(mode="after")
    def _validate_transcription_timeout(self) -> "Settings":
        """Assert TRANSCRIPTION_TIMEOUT_S leaves a 30 s cleanup window before soft kill.

        The invariant: TRANSCRIPTION_TIMEOUT_S < CELERY_SOFT_TIME_LIMIT_S - 30.
        If the transcription asyncio.wait_for fires at or after the soft limit,
        Celery's SIGPROF fires inside the still-blocked thread and the
        SoftTimeLimitExceeded never reaches our handler cleanly. The 30 s buffer
        is the industry-standard cleanup breathing room (Celery docs; Sidekiq sidekiq-cron).
        (Issue 105 — Fix 5)
        """
        ceiling = self.CELERY_SOFT_TIME_LIMIT_S - 30
        if ceiling <= self.TRANSCRIPTION_TIMEOUT_S:
            raise ValueError(
                f"TRANSCRIPTION_TIMEOUT_S ({self.TRANSCRIPTION_TIMEOUT_S}) must be less than "
                f"CELERY_SOFT_TIME_LIMIT_S - 30 = {ceiling}. "
                f"Increase CELERY_SOFT_TIME_LIMIT_S or decrease TRANSCRIPTION_TIMEOUT_S."
            )
        return self

    @model_validator(mode="after")
    def _require_prod_secrets(self) -> "Settings":
        # Fail fast in production if billing secrets are unset — otherwise the gap
        # surfaces only at first checkout/webhook (Issue 75). Dev/test (ENV defaults
        # to "development") is unaffected.
        if self.ENV == "production":
            missing = [
                name
                for name in ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET")
                if not getattr(self, name)
            ]
            if missing:
                raise ValueError(f"In production these must be set: {', '.join(missing)}")
            # The /metrics scrape surface must never be unauthenticated in production —
            # but don't crash-loop the whole app over a missing scrape token. Fail SAFE:
            # disable the endpoint and warn. Set METRICS_TOKEN to turn it back on. (Issue 76)
            if self.METRICS_ENABLED and not self.METRICS_TOKEN:
                logging.getLogger(__name__).warning(
                    "METRICS_TOKEN is unset in production — disabling /metrics. "
                    "Set METRICS_TOKEN to enable authenticated scraping."
                )
                self.METRICS_ENABLED = False
            # LOCAL_MEDIA_DIR must be absolute in production WHEN it's actually
            # used (i.e. STORAGE_BACKEND=local). With STORAGE_BACKEND=r2 the
            # local-disk path is dead config, so we don't crash-loop prod over
            # a stale ./media default in .env. Only check when the value is
            # actually load-bearing. (Issue 105 — Fix 7; relaxed for prod-r2
            # case after the initial deploy crash, Issue 110 hotfix)
            if (
                self.STORAGE_BACKEND == "local"
                and not Path(self.LOCAL_MEDIA_DIR).expanduser().is_absolute()
            ):
                raise ValueError(
                    f"LOCAL_MEDIA_DIR must be absolute in production when "
                    f"STORAGE_BACKEND=local; got {self.LOCAL_MEDIA_DIR!r}"
                )
            # Object storage is REQUIRED in production. The app and worker run as
            # separate containers with no shared media volume, so a local-disk
            # backend silently routes uploads to the app container where the worker
            # can never read them — every upload then FAILs in the pipeline. This
            # fail-fast turns that invisible misconfig into a startup error. The
            # four R2_* credentials must also resolve, or the first upload's R2 PUT
            # would 500 with no row created. (Root-caused from a prod FAILED upload
            # after the R2 instance was stood up but STORAGE_BACKEND stayed local.)
            if self.STORAGE_BACKEND != "r2":
                raise ValueError(
                    "STORAGE_BACKEND must be 'r2' in production: app and worker are "
                    "separate containers with no shared volume, so local-disk storage "
                    f"is unreadable by the worker. Got {self.STORAGE_BACKEND!r}."
                )
            missing_r2 = [
                name
                for name in (
                    "R2_ACCOUNT_ID",
                    "R2_ACCESS_KEY_ID",
                    "R2_SECRET_ACCESS_KEY",
                    "R2_BUCKET",
                )
                if not getattr(self, name)
            ]
            if missing_r2:
                raise ValueError(
                    "In production with STORAGE_BACKEND=r2 these must be set: "
                    f"{', '.join(missing_r2)}"
                )
        return self


try:
    settings = Settings()
except ValidationError as exc:
    missing = [str(e["loc"][0]) for e in exc.errors() if e["type"] == "missing"]
    if missing:
        print(
            f"[CreatorClip] Missing required environment variables: {', '.join(missing)}",
            file=sys.stderr,
        )
    else:
        print(f"[CreatorClip] Configuration error:\n{exc}", file=sys.stderr)
    sys.exit(1)
