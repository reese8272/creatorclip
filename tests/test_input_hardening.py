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
    )
    assert s.METRICS_ENABLED is True
    assert s.METRICS_TOKEN == "scrape-secret"
