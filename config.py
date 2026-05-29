import sys

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Required ---
    ANTHROPIC_API_KEY: str
    DATABASE_URL: str
    REDIS_URL: str

    # --- Postgres RLS (Issue 60) ---
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
