"""A Redis outage must degrade the product, not take it down (Issue 522).

The other Issue-522 tests drive the limiter and the spend guard directly. This
drives the **real FastAPI app** — real routes, real route-level dependencies,
real limiter — so the assertions are about what a creator's browser actually
receives, which is the only thing that was ever wrong.

Deliberately in the DEFAULT unit lane, not the integration lane. Nothing here
needs a live Postgres or Redis: the failure under test is Redis being *absent*,
and every layer is real except the address. A test that only ever runs in CI is
a weaker guarantee than one that runs on every developer's machine — and this
defect exists precisely because the failure path had no test anywhere.

Before this issue:
  * ``GET /auth/me`` (120/min, called by AuthGate on every page load) returned
    **500** whenever the limiter's Redis storage raised — a total sign-in outage
    that ``/health`` reported as 200.
  * The spend guard waved paid work through with the budget unread.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import redis.asyncio as aredis
from fastapi.testclient import TestClient

from tests._helpers import override_current_creator

# Port 1 is reserved and never listening: a real connect(), really refused.
_DEAD_REDIS_URI = "redis://127.0.0.1:1"


@pytest.fixture
def dead_redis():
    """A genuine redis.asyncio client whose every command fails fast."""
    return aredis.from_url(
        _DEAD_REDIS_URI,
        decode_responses=True,
        socket_timeout=0.25,
        socket_connect_timeout=0.25,
    )


@pytest.fixture
def app_client():
    """The real app with auth and the DB session overridden.

    The DB is stubbed so a 500 can only come from the Redis path under test — a
    database error would otherwise be indistinguishable from the outage
    behaviour these tests assert, which is how a passing test would end up
    proving nothing.
    """
    from auth import get_current_creator
    from db import get_session
    from main import app
    from models import OnboardingState

    creator = MagicMock()
    creator.id = uuid.uuid4()
    creator.channel_id = "UC_redis_outage"
    creator.channel_title = "Redis Outage"
    creator.email = "outage@example.com"
    creator.onboarding_state = OnboardingState.active
    creator.analysis_mode = MagicMock(value="auto")
    creator.created_at = MagicMock(isoformat=lambda: "2026-01-01T00:00:00")

    async def fake_session():
        session = AsyncMock()
        result = MagicMock()
        result.scalar_one.return_value = 1  # setup-step resolver's COUNT(*)
        result.scalar_one_or_none.return_value = None  # no YoutubeToken row
        session.execute = AsyncMock(return_value=result)
        yield session

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = fake_session
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()


def test_paid_route_returns_503_not_500_when_the_budget_cannot_be_read(
    app_client, dead_redis
) -> None:
    """Fail CLOSED, but as an honest 503 — never an unhandled 500, never a lie.

    POST /videos/{video_id}/clips/generate takes a PATH parameter and no body,
    deliberately chosen: there is no request payload to fail validation, so a 422
    can never masquerade as the refusal being asserted. The video_id need not
    exist — ``require_budget`` is a route-level dependency and runs before the
    handler looks anything up.
    """
    with patch("billing.spend_guard.get_redis_client", return_value=dead_redis):
        response = app_client.post(f"/videos/{uuid.uuid4()}/clips/generate")

    assert response.status_code != 500, (
        "an unreachable Redis produced an unhandled 500 — the spend guard's "
        "fail-closed arm is not reaching the real request path (Issue 522)"
    )
    assert response.status_code == 503, (
        f"expected 503 (budget unverifiable), got {response.status_code}. A 429 "
        "would tell the creator they are rate-limited when Redis is simply down."
    )
    detail = str(response.json().get("detail", "")).lower()
    assert "has been reached" not in detail, (
        "the creator must never be told a budget was reached that was never read"
    )
    assert "can't check" in detail or "cannot check" in detail


def test_sign_in_probe_survives_a_dead_limiter_backend(app_client) -> None:
    """``GET /auth/me`` is the product's front door — AuthGate calls it every page load.

    Patches the strategy object slowapi actually calls (``limiter.hit`` at
    extension.py:509). The fallback limiter is a *separate* FixedWindowRateLimiter
    instance, so it stays healthy and takes over — which is the behaviour under
    test. ``_storage_dead`` is process-global on the module-level limiter, so it
    is restored afterwards or every later test would run against the in-memory
    fallback.
    """
    from limiter import limiter

    original_hit = limiter._limiter.hit
    try:
        limiter._limiter.hit = MagicMock(side_effect=ConnectionError("redis down"))
        response = app_client.get("/auth/me")
    finally:
        limiter._limiter.hit = original_hit
        limiter._storage_dead = False

    assert response.status_code != 500, (
        "GET /auth/me returned 500 with the limiter storage unreachable. This is "
        "the Issue 522 sign-in outage: every rate-limited route 500s while "
        "/health still reports 200."
    )
