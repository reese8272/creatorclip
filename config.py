import sys

from pydantic import ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Required ---
    ANTHROPIC_API_KEY: str
    DATABASE_URL: str
    REDIS_URL: str
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
    ANTHROPIC_WEB_SEARCH_TOOL: str = "web_search_20250305"
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
    STORAGE_BACKEND: str = "local"
    R2_ACCOUNT_ID: str = ""
    R2_ACCESS_KEY_ID: str = ""
    R2_SECRET_ACCESS_KEY: str = ""
    R2_BUCKET: str = ""
    SOURCE_MEDIA_RETENTION_HOURS: int = 72
    CLIPS_PER_VIDEO_DEFAULT: int = 8
    MIN_VIDEOS_FOR_DNA: int = 10
    MIN_SHORTS_FOR_DNA: int = 5
    PERSONALIZATION_THRESHOLD_LABELS: int = 20
    # Max weight the preference model gets in the rerank blend once mature. Below
    # PERSONALIZATION_THRESHOLD_LABELS the weight is 0 (honest DNA-only fallback);
    # it ramps linearly to this cap by 2× the threshold. (Issue 60)
    PREFERENCE_WEIGHT_CAP: float = 0.5
    LLM_TIMEOUT_SECONDS: int = 120
    ENV: str = "development"
    YTDLP_ENABLED: bool = False
    YOUTUBE_QUOTA_DAILY_UNITS: int = 8000

    UPLOAD_MAX_MB: int = 500
    LOCAL_MEDIA_DIR: str = "./media"

    # ── Observability (Issue 75f) ───────────────────────────────────────────────
    # JSON structured logs (one object per line) for log aggregators. Defaults on;
    # set false for human-readable text in local dev.
    LOG_JSON: bool = True
    # Inbound header carrying a correlation id from an upstream proxy/gateway. If
    # absent or malformed, the middleware mints a UUID4. Echoed back on the response.
    REQUEST_ID_HEADER: str = "X-Request-ID"
    # Expose Prometheus golden-signal metrics at /metrics. Disable to drop the
    # endpoint entirely (the scrape surface) without touching the rest.
    METRICS_ENABLED: bool = True
    # Bearer token required to scrape /metrics. When set, callers must send
    # `Authorization: Bearer <token>`. Empty = unauthenticated (dev / internal-only
    # network); production fails fast below if metrics are enabled without it so the
    # operational scrape surface is never exposed unauthenticated. (Issue 76)
    METRICS_TOKEN: str = ""

    # ── Stripe billing ────────────────────────────────────────────────────────
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    STRIPE_PUBLISHABLE_KEY: str = ""
    APP_BASE_URL: str = "http://localhost:8000"
    FREE_TRIAL_MINUTES: int = 60

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
            # The /metrics scrape surface must not be unauthenticated in production:
            # set METRICS_TOKEN, or disable the endpoint with METRICS_ENABLED=false.
            if self.METRICS_ENABLED and not self.METRICS_TOKEN:
                raise ValueError(
                    "In production, set METRICS_TOKEN (or METRICS_ENABLED=false) — "
                    "the /metrics endpoint must not be exposed unauthenticated."
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
