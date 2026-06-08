"""Setup-step resolver + ``/auth/me`` + ``/creators/me`` integration.

Pins the resolver's branching so a refactor can't silently drop a step.
The DECISIONS 2026-06-08 entry documents the contract; these tests
enforce it. One assertion per ``setup_step`` value plus a check that
both endpoints carry the same nested object.
"""

import datetime
import uuid as _uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from auth import get_current_creator
from db import get_session
from main import app
from models import AnalysisMode, OnboardingState


def _override_creator(creator):
    from fastapi import Request

    async def _override(request: Request):
        request.state.creator_id = creator.id
        return creator

    return _override


def _mock_creator(state, *, channel_id="UC_test"):
    c = MagicMock()
    c.id = _uuid.uuid4()
    c.channel_id = channel_id
    c.channel_title = "Test"
    c.email = "t@t.co"
    c.onboarding_state = state
    c.analysis_mode = AnalysisMode.auto
    c.created_at = datetime.datetime(2026, 6, 8, tzinfo=datetime.UTC)
    return c


def _session_for(*, data_gate=None, video_count=0):
    """Build a session override whose ``execute`` returns either a data-gate
    count result or a clip-track video count depending on the resolver branch
    being exercised. The resolver only uses ONE shape per call, so we don't
    need to multiplex.
    """

    async def _session():
        session = AsyncMock()
        if data_gate is not None:
            # check_data_gate issues two scalar queries: long then short.
            calls = {"n": 0}

            async def _execute(_stmt):
                calls["n"] += 1
                r = MagicMock()
                r.scalar_one.return_value = (
                    data_gate["long"] if calls["n"] == 1 else data_gate["short"]
                )
                return r

            session.execute = _execute
        else:
            result = MagicMock()
            result.scalar_one.return_value = video_count
            session.execute = AsyncMock(return_value=result)
        yield session

    return _session


# ── Resolver direct (one test per branch) ───────────────────────────────────


@pytest.mark.parametrize(
    "state,data_gate,expected_step",
    [
        (OnboardingState.connected, {"long": 0, "short": 0}, "sync_catalog"),
        (OnboardingState.awaiting_data, {"long": 1, "short": 0}, "sync_catalog"),
        (OnboardingState.connected, {"long": 12, "short": 0}, "build_dna"),
        (OnboardingState.awaiting_data, {"long": 0, "short": 8}, "build_dna"),
    ],
)
def test_setup_step_pre_dna_branches(client, state, data_gate, expected_step):
    """connected/awaiting_data are disambiguated by check_data_gate.
    ``ready`` (either bucket ≥ its min) → build_dna; otherwise sync_catalog."""
    creator = _mock_creator(state)
    original = app.dependency_overrides.copy()
    app.dependency_overrides[get_current_creator] = _override_creator(creator)
    app.dependency_overrides[get_session] = _session_for(data_gate=data_gate)
    try:
        body = client.get("/auth/me").json()
    finally:
        app.dependency_overrides = original
    assert body["setup"]["step"] == expected_step
    assert body["setup"]["next_action_url"] == "/static/onboarding.html"


def test_setup_step_dna_pending_routes_to_profile(client):
    creator = _mock_creator(OnboardingState.dna_pending)
    original = app.dependency_overrides.copy()
    app.dependency_overrides[get_current_creator] = _override_creator(creator)
    # dna_pending does NOT call the DB — no session needed beyond the dep.
    app.dependency_overrides[get_session] = _session_for(video_count=0)
    try:
        body = client.get("/auth/me").json()
    finally:
        app.dependency_overrides = original
    setup = body["setup"]
    assert setup["step"] == "confirm_dna"
    assert setup["next_action_type"] == "navigate"
    assert setup["next_action_url"].startswith("/static/profile.html")


def test_setup_step_active_no_videos_routes_to_link_form(client):
    """``active`` + zero clip-track videos → link_first_video with the
    open_form action_type so the dashboard expands the inline form."""
    creator = _mock_creator(OnboardingState.active)
    original = app.dependency_overrides.copy()
    app.dependency_overrides[get_current_creator] = _override_creator(creator)
    app.dependency_overrides[get_session] = _session_for(video_count=0)
    try:
        body = client.get("/auth/me").json()
    finally:
        app.dependency_overrides = original
    setup = body["setup"]
    assert setup["step"] == "link_first_video"
    assert setup["next_action_type"] == "open_form"


def test_setup_step_complete_when_active_with_videos(client):
    creator = _mock_creator(OnboardingState.active)
    original = app.dependency_overrides.copy()
    app.dependency_overrides[get_current_creator] = _override_creator(creator)
    app.dependency_overrides[get_session] = _session_for(video_count=3)
    try:
        body = client.get("/auth/me").json()
    finally:
        app.dependency_overrides = original
    assert body["setup"]["step"] == "complete"
    assert body["setup"]["progress_index"] == body["setup"]["progress_total"]


# ── Both endpoints return the same shape ────────────────────────────────────


def test_creators_me_and_auth_me_carry_identical_setup_block(client):
    creator = _mock_creator(OnboardingState.dna_pending)
    original = app.dependency_overrides.copy()
    app.dependency_overrides[get_current_creator] = _override_creator(creator)
    app.dependency_overrides[get_session] = _session_for(video_count=0)
    try:
        auth_body = client.get("/auth/me").json()
        me_body = client.get("/creators/me").json()
    finally:
        app.dependency_overrides = original
    assert auth_body["setup"] == me_body["setup"], (
        "auth.js stashes window.__SETUP__ on /auth/me and other pages read "
        "the same field off /creators/me — they MUST agree, otherwise the "
        "dashboard and profile page disagree on what to nag the user about."
    )
