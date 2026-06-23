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
    # LLM cost constants (USD per million tokens, standard tier).
    # Source: Anthropic pricing page (platform.claude.com, fetched 2026-06-23).
    # Override via env var to pick up price changes without a code deploy.
    # (Issue 220 — Usage cost ledger)
    COST_PER_MTOK_IN_SONNET: float = 3.0   # Sonnet 4.6 input: $3/MTok standard
    COST_PER_MTOK_OUT_SONNET: float = 15.0  # Sonnet 4.6 output: $15/MTok standard
    COST_PER_MTOK_IN_HAIKU: float = 1.0    # Haiku 4.5 input: $1/MTok standard
    COST_PER_MTOK_OUT_HAIKU: float = 5.0   # Haiku 4.5 output: $5/MTok standard
    # Cache-read multiplier: prompt-cache hits are billed at 10% of the base input rate.
    # Source: platform.claude.com/docs/en/about-claude/pricing (fetched 2026-06-23).
    COST_CACHE_READ_MULTIPLIER: float = 0.1
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
    STORAGE_BACKEND: str = "local"
    R2_ACCOUNT_ID: str = ""
    R2_ACCESS_KEY_ID: str = ""
    R2_SECRET_ACCESS_KEY: str = ""
    R2_BUCKET: str = ""
    SOURCE_MEDIA_RETENTION_HOURS: int = 72
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
    # Issue 126 — trial window (calendar days). Set on first OAuth login; the
    # 402 paywall reads it live + the dashboard banner counts down against it.
    TRIAL_DURATION_DAYS: int = 7
    # Threshold below which the dashboard nav chip lights up amber + pre-action
    # warnings render before Generate / Queue. Tuneable without a deploy.
    LOW_BALANCE_THRESHOLD_MINUTES: int = 10
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
