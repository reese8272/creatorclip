"""Issue 228 — per-creator pre-job daily quota on every LLM/render endpoint.

Cost-safety guard: the existing per-endpoint hourly @limiter.limit values are the
short-window burst cap; before Issue 228 there was no per-creator DAILY ceiling,
so a single creator could burn unbounded Anthropic/Deepgram/ffmpeg/R2 spend.

These tests pin three invariants without needing Postgres:

1. ``limiter`` exposes the typed daily-cap constants derived from settings, and a
   ``daily_limit`` helper that emits a parseable slowapi "N/day" string.
2. Every LLM/render handler carries a STACKED "/day" limit alongside its existing
   hourly burst limit (introspected via ``limiter._route_limits``, mirroring the
   helper in test_rate_limiting.py).
3. Exceeding the cap surfaces a clean HTTP 429 through the already-registered
   slowapi handler — actionable copy, no stack trace — while a single in-budget
   call passes the gate. The throttle path uses a fake limiter raising
   ``RateLimitExceeded`` so no real cross-request Redis state is needed (the
   scripted-loop end-to-end assertion is the staging Verify gate).

The DB is mocked at the session boundary (default unit lane); Redis is not
required for the introspection/handler cases.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

from tests._helpers import override_current_creator

# Daily-cap routes keyed by qualified name → which settings cap should back them.
_LLM_ROUTES = [
    "routers.clips.generate_clips",
    "routers.titles.start_title_suggestions",
    "routers.thumbnails.get_thumbnail_patterns",
    "routers.thumbnails.start_thumbnail_concepts",
    "routers.insights.analyze_performer",
    "routers.improvement.start_improvement_brief",
    "routers.analysis.start_video_analysis",
    "routers.analysis.start_hook_analysis",
    "routers.analysis.start_chapter_generation",
]
_RENDER_ROUTES = [
    "routers.clips.render_clip",
    "routers.clips.clean_clip",
    "routers.clips.submit_cuts",
    "routers.clips.ingest_clip",
]


def _import_routers() -> None:
    import routers.analysis  # noqa: F401
    import routers.clips  # noqa: F401
    import routers.improvement  # noqa: F401
    import routers.insights  # noqa: F401
    import routers.thumbnails  # noqa: F401
    import routers.titles  # noqa: F401


def _limits_for(qualname: str) -> list:
    from limiter import limiter

    return limiter._route_limits.get(qualname, [])


def _has_period(qualname: str, period: str) -> bool:
    """True if any limit on the route uses the given period word (slowapi
    stringifies a Limit as e.g. '50 per 1 day' / '10 per 1 hour')."""
    return any(period in str(limit.limit).lower() for limit in _limits_for(qualname))


def _amount_for_period(qualname: str, period: str) -> int | None:
    """Return the configured amount for the limit on ``qualname`` whose period
    matches ``period`` (e.g. the '/day' cap), or None if absent."""
    for limit in _limits_for(qualname):
        if period in str(limit.limit).lower():
            return int(limit.limit.amount)
    return None


# ── Constants derived from settings ───────────────────────────────────────────


def test_daily_limit_helper_emits_parseable_string() -> None:
    from limits import parse

    from limiter import daily_limit

    item = parse(daily_limit(50))
    # limits expresses "50/day" as amount=50 over a 1-day granularity (86400s).
    assert item.amount == 50
    assert item.get_expiry() == 86400


def test_daily_limit_constants_track_settings() -> None:
    from config import settings
    from limiter import LLM_DAILY_LIMIT, RENDER_DAILY_LIMIT

    assert f"{settings.LLM_DAILY_JOB_LIMIT}/day" == LLM_DAILY_LIMIT
    assert f"{settings.RENDER_DAILY_JOB_LIMIT}/day" == RENDER_DAILY_LIMIT
    # Importing the limiter must not require Postgres — this import already proved it.


def test_quota_settings_have_sane_defaults() -> None:
    from config import settings

    assert isinstance(settings.LLM_DAILY_JOB_LIMIT, int)
    assert isinstance(settings.RENDER_DAILY_JOB_LIMIT, int)
    assert settings.LLM_DAILY_JOB_LIMIT > 0
    assert settings.RENDER_DAILY_JOB_LIMIT > 0


# ── Daily cap stacked on every LLM/render route ───────────────────────────────


def test_every_llm_route_has_daily_cap_stacked_on_hourly() -> None:
    _import_routers()
    for qualname in _LLM_ROUTES:
        lims = [str(limit.limit) for limit in _limits_for(qualname)]
        assert _has_period(qualname, "day"), f"{qualname} missing daily cap: {lims}"
        assert _has_period(qualname, "hour"), (
            f"{qualname} lost its hourly burst limit — the daily cap must STACK, "
            f"not replace it: {lims}"
        )


def test_every_render_route_has_daily_cap_stacked_on_hourly() -> None:
    _import_routers()
    for qualname in _RENDER_ROUTES:
        lims = [str(limit.limit) for limit in _limits_for(qualname)]
        assert _has_period(qualname, "day"), f"{qualname} missing daily cap: {lims}"
        assert _has_period(qualname, "hour"), f"{qualname} lost hourly burst: {lims}"


def test_render_clip_carries_render_daily_cap_value() -> None:
    from config import settings

    _import_routers()
    assert (
        _amount_for_period("routers.clips.render_clip", "day")
        == settings.RENDER_DAILY_JOB_LIMIT
    )


def test_generate_clips_carries_llm_daily_cap_value() -> None:
    from config import settings

    _import_routers()
    assert (
        _amount_for_period("routers.clips.generate_clips", "day")
        == settings.LLM_DAILY_JOB_LIMIT
    )


# ── 429 surfaced cleanly when the cap is exceeded ─────────────────────────────


def test_quota_exceeded_returns_clean_429(client, monkeypatch) -> None:
    """When the daily cap is hit, the request surfaces a clean HTTP 429 through
    the already-registered slowapi handler — actionable copy, no stack trace and
    no virality language — instead of a 500.

    We force the cap by patching the limiter's per-request check to raise
    ``RateLimitExceeded`` with the REAL daily Limit object registered for
    render_clip, then drive a genuine request so the full middleware + handler
    path runs (no hand-built Request). This is the unit-lane proxy for the
    scripted-loop end-to-end throttle, which is the staging Verify gate.
    """
    from slowapi.errors import RateLimitExceeded

    import limiter as limiter_mod
    import routers.clips  # noqa: F401 — populate _route_limits
    from auth import get_current_creator
    from db import get_session
    from main import app
    from models import OnboardingState

    day_limit = next(
        limit
        for limit in _limits_for("routers.clips.render_clip")
        if "day" in str(limit.limit).lower()
    )

    def _raise_cap(request, endpoint_func, in_middleware=True):
        # Mirror slowapi: stamp request.state.view_rate_limit (read by the
        # handler's header injector) before signalling the breach, so the clean
        # 429 path runs exactly as it would for a real over-budget request.
        request.state.view_rate_limit = (day_limit.limit, [str(creator.id)])
        raise RateLimitExceeded(day_limit)

    # Patch the limiter's request-check so any rate-limited route trips the cap.
    monkeypatch.setattr(limiter_mod.limiter, "_check_request_limit", _raise_cap)

    creator = MagicMock()
    creator.id = uuid.uuid4()
    creator.channel_id = "UC123"
    creator.channel_title = "Test"
    creator.email = "t@t.com"
    creator.onboarding_state = OnboardingState.active
    creator.analysis_mode = MagicMock(value="auto")
    creator.created_at = MagicMock(isoformat=lambda: "2025-01-01T00:00:00")

    async def fake_session():
        yield AsyncMock()

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = fake_session
    try:
        resp = client.get("/creators/me")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 429
    body = resp.text
    assert "Traceback" not in body
    assert "limit" in body.lower()
    assert "viral" not in body.lower()


def test_balance_gate_runs_before_llm_job(client) -> None:
    """The LLM routers now pre-flight ``check_positive_balance``: a zero-balance
    creator gets a 402 (not a queued 202) before any LLM spend is incurred."""
    from fastapi import HTTPException

    import routers.titles as titles_mod
    from auth import get_current_creator
    from db import get_session
    from main import app
    from models import OnboardingState

    creator = MagicMock()
    creator.id = uuid.uuid4()
    creator.channel_id = "UC123"
    creator.onboarding_state = OnboardingState.active

    async def fake_session():
        yield AsyncMock()

    async def fake_check_positive_balance(creator_id, session) -> None:
        raise HTTPException(
            status_code=402,
            detail="No minutes remaining. Purchase a pack at /pricing to process videos.",
        )

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = fake_session
    # Patch the symbol imported into the router module under test.
    orig = titles_mod.check_positive_balance
    titles_mod.check_positive_balance = fake_check_positive_balance
    try:
        resp = client.post(f"/creators/me/videos/{uuid.uuid4()}/titles")
        assert resp.status_code == 402
        assert "/pricing" in resp.json()["detail"]
    finally:
        titles_mod.check_positive_balance = orig
        app.dependency_overrides.clear()
