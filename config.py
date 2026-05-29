import sys

from pydantic import ValidationError
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
    LLM_TIMEOUT_SECONDS: int = 120
    ENV: str = "development"
    YTDLP_ENABLED: bool = False
    YOUTUBE_QUOTA_DAILY_UNITS: int = 8000

    UPLOAD_MAX_MB: int = 500
    LOCAL_MEDIA_DIR: str = "./media"

    # ── Stripe billing ────────────────────────────────────────────────────────
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    STRIPE_PUBLISHABLE_KEY: str = ""
    APP_BASE_URL: str = "http://localhost:8000"
    FREE_TRIAL_MINUTES: int = 60


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
