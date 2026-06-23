"""Unit tests for Issue 235 funnel instrumentation.

Tests the clip_kept idempotency logic and the PII-free guarantee on the
new event property shapes.  All tests run without a database (DB-free per
the no-Docker constraint; the queryable DB sink itself is staging-pending).

Coverage:
- _KEEP_ACTIONS: upvote/trim/format in set; downvote/skip excluded
- _is_first_keep: True when no prior keep, False when one exists
- clip_kept gate: is_activation flag set correctly based on action + prior-keep state
- _redact() strips accidental PII from new event property shapes
"""

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from event_log import _redact
from models import FeedbackAction
from routers.review import _KEEP_ACTIONS, _is_first_keep

# ── _KEEP_ACTIONS constant ───────────────────────────────────────────────────


def test_keep_actions_contains_upvote_trim_format():
    """The activation set must include exactly the three positive signals."""
    assert FeedbackAction.upvote in _KEEP_ACTIONS
    assert FeedbackAction.trim in _KEEP_ACTIONS
    assert FeedbackAction.format in _KEEP_ACTIONS


def test_keep_actions_excludes_downvote_skip():
    """Rejection actions must not be treated as activation signals."""
    assert FeedbackAction.downvote not in _KEEP_ACTIONS
    assert FeedbackAction.skip not in _KEEP_ACTIONS


# ── _is_first_keep unit tests (mocked session) ──────────────────────────────


@pytest.mark.asyncio
async def test_is_first_keep_true_when_no_prior_keep():
    """When no prior keep action exists, _is_first_keep returns True."""
    session = AsyncMock()
    result = MagicMock()
    result.scalar.return_value = False  # EXISTS returns false → no prior row
    session.execute = AsyncMock(return_value=result)

    creator_id = uuid.uuid4()
    assert await _is_first_keep(session, creator_id) is True


@pytest.mark.asyncio
async def test_is_first_keep_false_when_prior_keep_exists():
    """When a prior keep action exists, _is_first_keep returns False."""
    session = AsyncMock()
    result = MagicMock()
    result.scalar.return_value = True  # EXISTS returns true → prior row found
    session.execute = AsyncMock(return_value=result)

    creator_id = uuid.uuid4()
    assert await _is_first_keep(session, creator_id) is False


# ── clip_kept gate logic — tests the is_activation predicate directly ────────
# These test the gate logic without going through the full HTTP handler,
# because ensure_future scheduling in a sync TestClient is unreliable.
# The idempotency contract is in _is_first_keep (tested above) + the
# `is_activation = body.action in _KEEP_ACTIONS and await _is_first_keep(...)`
# guard in submit_feedback.


@pytest.mark.asyncio
async def test_gate_fires_for_first_upvote():
    """upvote + no prior keep → is_activation should be True."""
    session = AsyncMock()
    result = MagicMock()
    result.scalar.return_value = False  # no prior keep
    session.execute = AsyncMock(return_value=result)

    creator_id = uuid.uuid4()
    action = FeedbackAction.upvote
    is_activation = action in _KEEP_ACTIONS and await _is_first_keep(session, creator_id)
    assert is_activation is True


@pytest.mark.asyncio
async def test_gate_suppressed_on_second_keep():
    """upvote + prior keep exists → is_activation should be False (idempotent)."""
    session = AsyncMock()
    result = MagicMock()
    result.scalar.return_value = True  # prior keep exists
    session.execute = AsyncMock(return_value=result)

    creator_id = uuid.uuid4()
    action = FeedbackAction.upvote
    is_activation = action in _KEEP_ACTIONS and await _is_first_keep(session, creator_id)
    assert is_activation is False


@pytest.mark.asyncio
async def test_gate_never_fires_for_downvote():
    """downvote must never produce is_activation=True, even without prior keeps."""
    session = AsyncMock()
    result = MagicMock()
    result.scalar.return_value = False  # no prior keep — would fire if action were keep
    session.execute = AsyncMock(return_value=result)

    creator_id = uuid.uuid4()
    action = FeedbackAction.downvote
    # Short-circuit: downvote not in _KEEP_ACTIONS → _is_first_keep never called
    is_activation = action in _KEEP_ACTIONS and await _is_first_keep(session, creator_id)
    assert is_activation is False
    # Confirm _is_first_keep was never called (short-circuit evaluation)
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_gate_fires_for_trim_first_time():
    """trim (editorial keep) must fire activation on first occurrence."""
    session = AsyncMock()
    result = MagicMock()
    result.scalar.return_value = False
    session.execute = AsyncMock(return_value=result)

    creator_id = uuid.uuid4()
    action = FeedbackAction.trim
    is_activation = action in _KEEP_ACTIONS and await _is_first_keep(session, creator_id)
    assert is_activation is True


@pytest.mark.asyncio
async def test_gate_fires_for_format_first_time():
    """format (deliberate render) must fire activation on first occurrence."""
    session = AsyncMock()
    result = MagicMock()
    result.scalar.return_value = False
    session.execute = AsyncMock(return_value=result)

    creator_id = uuid.uuid4()
    action = FeedbackAction.format
    is_activation = action in _KEEP_ACTIONS and await _is_first_keep(session, creator_id)
    assert is_activation is True


# ── record_event noop sanity — ensure the event sink is disabled in tests ────


def test_record_event_noop_when_db_disabled(monkeypatch):
    """With EVENT_LOG_DB_ENABLED=False, record_event returns without touching
    the pool — proves funnel calls are safe in DB-free test environments."""
    import event_log

    monkeypatch.setattr(event_log.settings, "EVENT_LOG_DB_ENABLED", False)
    monkeypatch.setattr(event_log, "_sessionmaker", None)
    asyncio.run(
        event_log.record_event(
            source="backend",
            event="clip_kept",
            creator_id=uuid.uuid4(),
            extra={"action": "upvote"},
        )
    )
    assert event_log._sessionmaker is None  # pool never opened


# ── PII-free assertion on new event property shapes ──────────────────────────


def test_redact_strips_accidental_pii_from_funnel_event_properties():
    """_redact() must mask any accidental email/token keys in funnel event extra.

    This verifies that the _redact() boundary in event_log.record_event covers
    the specific property shapes used by the Issue 235 call sites.
    """
    extra_with_pii = {
        "is_new": True,
        "email": "leak@example.com",  # must be redacted
        "action": "upvote",
        "niche_count": 2,
    }
    out = _redact(extra_with_pii)
    assert out is not None
    assert out["email"] == "[redacted]"
    # Safe fields pass through
    assert out["is_new"] is True
    assert out["action"] == "upvote"
    assert out["niche_count"] == 2


def test_redact_clean_funnel_properties_pass_through():
    """Verify the actual property shapes emitted by Issue 235 call sites
    contain no sensitive keys that would be masked by _redact()."""
    # oauth_completed extra
    assert _redact({"is_new": True}) == {"is_new": True}

    # identity_saved extra
    assert _redact({"niche_count": 3}) == {"niche_count": 3}

    # dna_confirmed extra
    assert _redact({"version": 1}) == {"version": 1}

    # clip_kept extra
    assert _redact({"action": "upvote"}) == {"action": "upvote"}
