"""Issue 203 — data-gate unlock deltas + evaluation event.

Pins:
  1. `check_data_gate` returns server-computed `remaining_long_form` /
     `remaining_shorts` = max(0, threshold - count), so the frontend never
     re-derives the MIN_*_FOR_DNA thresholds.
  2. At exactly the threshold the remaining count hits 0 AND the ready flag
     flips in the same response (no off-by-one waiting room).
  3. GET /creators/me/data-gate emits a `data_gate_evaluated` event
     (ready + both counts) through the event_log DB sink.

All DB-free: session mocked at the execute boundary, same pattern as
tests/test_issue_88_filter_parity.py / tests/test_youtube_edges.py.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from auth import get_current_creator
from db import get_session
from main import app
from tests._helpers import override_current_creator


def _fake_session(long_count: int, short_count: int) -> MagicMock:
    """check_data_gate issues two scalar COUNTs: longs first, then shorts."""
    counts = iter([long_count, short_count])

    async def _exec(_stmt):
        r = MagicMock()
        r.scalar_one.return_value = next(counts)
        return r

    session = MagicMock()
    session.execute = _exec
    return session


# ── remaining-count math ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_check_data_gate_reports_remaining_counts():
    """Below both thresholds (defaults 10 long / 5 short): positive deltas."""
    from youtube.analytics import check_data_gate

    out = await check_data_gate(_fake_session(8, 3), uuid.uuid4())
    assert out["remaining_long_form"] == 2
    assert out["remaining_shorts"] == 2
    assert out["ready"] is False


@pytest.mark.asyncio
async def test_check_data_gate_remaining_zero_at_threshold():
    """At exactly the threshold: remaining hits 0 and ready flips True."""
    from youtube.analytics import check_data_gate

    out = await check_data_gate(_fake_session(10, 5), uuid.uuid4())
    assert out["remaining_long_form"] == 0
    assert out["remaining_shorts"] == 0
    assert out["long_form_ready"] is True
    assert out["shorts_ready"] is True
    assert out["ready"] is True


@pytest.mark.asyncio
async def test_check_data_gate_remaining_clamped_above_threshold():
    """Over-threshold longs clamp to 0 (never negative); the short bucket
    still reports its own positive delta — deltas are per-kind."""
    from youtube.analytics import check_data_gate

    out = await check_data_gate(_fake_session(15, 0), uuid.uuid4())
    assert out["remaining_long_form"] == 0
    assert out["remaining_shorts"] == 5
    assert out["ready"] is True, "OR semantics: long-only creator IS ready"


# ── data_gate_evaluated event from the endpoint ───────────────────────────────


def test_data_gate_endpoint_emits_evaluation_event(client):
    """GET /creators/me/data-gate fires data_gate_evaluated with ready + both
    counts via the event_log DB sink (fire-and-forget: ensure_future calls
    record_event(...) synchronously to build the coroutine, so call args are
    captured even if the scheduled task hasn't completed)."""
    creator = MagicMock()
    creator.id = uuid.uuid4()

    async def _override_session():
        yield _fake_session(8, 3)

    record_mock = AsyncMock()
    original = app.dependency_overrides.copy()
    # Canonical override from tests/_helpers — also stashes creator_id on
    # request.state for the slowapi creator_key. (A locally-defined override
    # annotated `request: Request` breaks under this module's
    # `from __future__ import annotations`: FastAPI can't resolve the string
    # annotation from the function's globals and turns `request` into a
    # required query param → 422.)
    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = _override_session
    try:
        with patch("event_log.record_event", new=record_mock):
            body = client.get("/creators/me/data-gate").json()
    finally:
        app.dependency_overrides = original

    assert body["remaining_long_form"] == 2
    assert body["remaining_shorts"] == 2
    assert body["ready"] is False

    # The request-logging middleware records an `http_request` event through the
    # same sink — filter for ours instead of asserting a single call.
    gate_calls = [
        c for c in record_mock.call_args_list if c.kwargs.get("event") == "data_gate_evaluated"
    ]
    assert len(gate_calls) == 1
    kwargs = gate_calls[0].kwargs
    assert kwargs["creator_id"] == creator.id
    assert kwargs["extra"] == {"ready": False, "long_form_videos": 8, "shorts": 3}
