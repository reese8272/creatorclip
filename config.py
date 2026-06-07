import logging
import sys
from pathlib import Path

from pydantic import ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    # web_search_20260209 is the GA version with dynamic filtering: Claude
    # writes code to pre-filter search results before they reach the context
    # window, reducing tokens read and improving accuracy. Same tool API
    # shape as the prior _20250305 (`name: "web_search"`); no call-site
    # changes required to swap. (Issue 84)
    ANTHROPIC_WEB_SEARCH_TOOL: str = "web_search_20260209"
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
    STORAGE_BACKEND: str = "local"
    R2_ACCOUNT_ID: str = ""
    R2_ACCESS_KEY_ID: str = ""
    R2_SECRET_ACCESS_KEY: str = ""
    R2_BUCKET: str = ""
    SOURCE_MEDIA_RETENTION_HOURS: int = 72
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

    CLIPS_PER_VIDEO_DEFAULT: int = 8
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
    PERSONALIZATION_THRESHOLD_LABELS: int = 20
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

    UPLOAD_MAX_MB: int = 500
    LOCAL_MEDIA_DIR: str = "./media"

    # Celery soft-time-limit as a config value so the transcription-timeout
    # validator below can assert the 30s cleanup-breathing-room invariant
    # without importing celery_app (which would create a circular import).
    # Must stay in sync with task_soft_time_limit in worker/celery_app.py.
    # (Issue 105 — Fix 5)
    CELERY_SOFT_TIME_LIMIT_S: int = 3000

    # ── Observability (Issue 75f / Issue 122) ──────────────────────────────────
    # JSON structured logs (one object per line) for log aggregators. Defaults on;
    # set false for human-readable text in local dev.
    LOG_JSON: bool = True
    # Directory for persistent log files. Defaults to /app/logs which maps to
    # ./logs on the host via the .:/app Docker volume — readable after a session
    # ends without any extra mount. Set to "" to disable file logging.
    LOG_DIR: str = "/app/logs"
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
    # Per-request HTTP timeout for the Stripe SDK. Default SDK timeout is ~80s;
    # one stuck call would pin an asyncio.to_thread executor slot for that long.
    # Scale-checklist E (backpressure): every external call needs a bounded
    # timeout. (Issue 106)
    STRIPE_TIMEOUT_S: int = 10

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
