"""
Unit tests for Batch 8 input/config hardening (Issues 73 + 75). DB-free.
"""

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

# ── Issue 73: youtube_video_id validation ───────────────────────────────────────


def test_validate_youtube_id_accepts_valid_and_rejects_bad():
    from routers.videos import _validate_youtube_id

    _validate_youtube_id("dQw4w9WgXcQ")  # 11 valid chars → no raise

    for bad in ("../etc/passwd", "short", "toolongtoolong", "has/slash11", ""):
        with pytest.raises(HTTPException) as exc:
            _validate_youtube_id(bad)
        assert exc.value.status_code == 422


# ── Issue 75: upload_intel must not IndexError on a malformed row ───────────────


class _Row:
    def __init__(self, dow, hour, idx):
        self.day_of_week = dow
        self.hour = hour
        self.activity_index = idx


def test_best_upload_windows_skips_malformed_rows():
    from upload_intel.timing import best_upload_windows

    # day_of_week=7 and hour=25 are out of range — must be dropped, not IndexError → 500.
    rows = [_Row(7, 25, 0.9), _Row(2, 14, 0.5)]
    out = best_upload_windows(rows, top_n=3)
    assert len(out) == 1
    assert out[0]["day_of_week"] == 2


# ── Issue 75: production fails fast without Stripe secrets ──────────────────────


def test_production_requires_stripe_secrets():
    from config import Settings

    # conftest sets the other required vars in the environment; Stripe is unset.
    with pytest.raises(ValidationError):
        Settings(ENV="production")


def test_development_ok_without_stripe_secrets():
    from config import Settings

    settings = Settings(ENV="development")
    assert settings.ENV == "development"


# ── Issue 76: /metrics is never exposed unauthenticated in production ──────────


def test_production_without_metrics_token_auto_disables_metrics():
    """Fail safe, not crash: a missing scrape token disables /metrics, never raises."""
    from config import Settings

    s = Settings(
        ENV="production",
        STRIPE_SECRET_KEY="sk_live_x",
        STRIPE_WEBHOOK_SECRET="whsec_x",
        METRICS_ENABLED=True,
        METRICS_TOKEN="",
        LOCAL_MEDIA_DIR="/var/lib/creatorclip/media",  # Issue 105 Fix 7: must be absolute in prod
        # Object storage is required in production (app/worker have no shared volume).
        STORAGE_BACKEND="r2",
        R2_ACCOUNT_ID="acct",
        R2_ACCESS_KEY_ID="ak",
        R2_SECRET_ACCESS_KEY="sk",
        R2_BUCKET="bucket",
    )
    assert s.METRICS_ENABLED is False  # auto-disabled, so the endpoint isn't registered


def test_production_keeps_metrics_enabled_with_token():
    from config import Settings

    s = Settings(
        ENV="production",
        STRIPE_SECRET_KEY="sk_live_x",
        STRIPE_WEBHOOK_SECRET="whsec_x",
        METRICS_ENABLED=True,
        METRICS_TOKEN="scrape-secret",
        LOCAL_MEDIA_DIR="/var/lib/creatorclip/media",  # Issue 105 Fix 7: must be absolute in prod
        # Object storage is required in production (app/worker have no shared volume).
        STORAGE_BACKEND="r2",
        R2_ACCOUNT_ID="acct",
        R2_ACCESS_KEY_ID="ak",
        R2_SECRET_ACCESS_KEY="sk",
        R2_BUCKET="bucket",
    )
    assert s.METRICS_ENABLED is True
    assert s.METRICS_TOKEN == "scrape-secret"


# ── Storage hardening: object storage is required in production ────────────────
# Root-caused from a prod upload that silently FAILED — app/worker are separate
# containers with no shared media volume, so a local-disk backend is unreadable
# by the worker. The config validator now fails fast on that misconfig.


def _prod_kwargs(**over):
    base = dict(
        ENV="production",
        STRIPE_SECRET_KEY="sk_live_x",
        STRIPE_WEBHOOK_SECRET="whsec_x",
        LOCAL_MEDIA_DIR="/var/lib/creatorclip/media",
        STORAGE_BACKEND="r2",
        R2_ACCOUNT_ID="acct",
        R2_ACCESS_KEY_ID="ak",
        R2_SECRET_ACCESS_KEY="sk",
        R2_BUCKET="bucket",
    )
    base.update(over)
    return base


def test_production_rejects_local_storage_backend():
    from config import Settings

    with pytest.raises(ValidationError, match="STORAGE_BACKEND"):
        Settings(**_prod_kwargs(STORAGE_BACKEND="local"))


def test_production_r2_requires_all_credentials():
    from config import Settings

    with pytest.raises(ValidationError, match="R2_SECRET_ACCESS_KEY"):
        Settings(**_prod_kwargs(R2_SECRET_ACCESS_KEY=""))


def test_production_r2_with_full_config_is_valid():
    from config import Settings

    s = Settings(**_prod_kwargs())
    assert s.STORAGE_BACKEND == "r2"
    assert s.R2_BUCKET == "bucket"


# ── Issue 352 Batch A: boot-time fail-fast on ENV typos and weak JWT secrets ───


def test_env_literal_rejects_typo():
    """A deploy-time typo like 'prod' must fail at boot, not silently run a
    production container with dev-mode hardening (no HSTS, /docs exposed)."""
    from config import Settings

    with pytest.raises(ValidationError, match="ENV"):
        Settings(ENV="prod")


def test_env_literal_accepts_staging():
    from config import Settings

    s = Settings(ENV="staging")
    assert s.ENV == "staging"


def test_jwt_secret_key_rejects_short_value():
    """HS256 needs >= 32 bytes (RFC 7518 §3.2); a short secret is forgeable."""
    from config import Settings

    with pytest.raises(ValidationError, match="JWT_SECRET_KEY"):
        Settings(JWT_SECRET_KEY="too-short")
