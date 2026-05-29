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


# ── Tier-1 pre-launch: production locks ALLOWED_ORIGINS ─────────────────────────

# Both prod secrets passed so these isolate the CORS validator (Stripe is satisfied).
_PROD = {"STRIPE_SECRET_KEY": "sk_test_x", "STRIPE_WEBHOOK_SECRET": "whsec_x"}


def test_production_rejects_localhost_origin():
    from config import Settings

    # conftest's default ALLOWED_ORIGINS is http://localhost:8000 — invalid for prod.
    with pytest.raises(ValidationError):
        Settings(ENV="production", **_PROD)


def test_production_rejects_wildcard_origin():
    from config import Settings

    with pytest.raises(ValidationError):
        Settings(ENV="production", ALLOWED_ORIGINS="*", **_PROD)


def test_production_rejects_plain_http_origin():
    from config import Settings

    with pytest.raises(ValidationError):
        Settings(ENV="production", ALLOWED_ORIGINS="http://agenticlip.studio", **_PROD)


def test_production_accepts_https_domain_origin():
    from config import Settings

    settings = Settings(ENV="production", ALLOWED_ORIGINS="https://agenticlip.studio", **_PROD)
    assert settings.ALLOWED_ORIGINS == "https://agenticlip.studio"
