"""Unit-lane coverage for the Issue-473 watermark and the Issue-475 loader.

The full behavior is proven in the integration lane (real Postgres window
functions); these tests walk the same code paths with mocked sessions so the
changed lines are visible to the unit-lane coverage XML that diff-cover reads.
"""

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio


async def test_retrain_skips_when_no_new_verdicts_and_no_new_outcomes(caplog):
    """Both watermark legs run (verdict count, then the outcome-join count) and
    the task no-ops when neither moved — the Issue-473 self-debounce."""
    from worker.tasks import _retrain_preference_async

    latest = MagicMock()
    latest.updated_at = datetime.now(UTC)

    r_model = MagicMock()
    r_model.scalars.return_value.first.return_value = latest
    r_verdicts = MagicMock()
    r_verdicts.scalar_one.return_value = 0
    r_outcomes = MagicMock()
    r_outcomes.scalar_one.return_value = 0

    session = MagicMock()
    session.execute = AsyncMock(side_effect=[r_model, r_verdicts, r_outcomes])

    @asynccontextmanager
    async def _fake_tenant_session(_cid):
        yield session

    with (
        patch("db.tenant_session", _fake_tenant_session),
        patch("worker.tasks._try_advisory_lock", new=AsyncMock(return_value=True)),
        patch("worker.tasks._rollback_then_unlock", new=AsyncMock()),
        caplog.at_level("INFO", logger="worker.tasks"),
    ):
        await _retrain_preference_async(str(uuid.uuid4()))

    assert session.execute.await_count == 3, "outcome watermark leg must run when verdicts == 0"
    assert "no new verdicts or outcomes" in caplog.text


async def test_load_labeled_clips_maps_rows_and_filters_untrainable():
    """The loader consumes (feedback, clip, outcome) rows from the shared
    training select: outcome flags carry through, and a skip latest-verdict is
    excluded even with a good outcome (action-first relevance, Issue 475)."""
    from preference.efficacy import load_labeled_clips

    creator_id = uuid.uuid4()
    good_clip = SimpleNamespace(
        id=uuid.uuid4(),
        dna_match=0.6,
        score=0.7,
        signals_jsonb={"features": {"signal_density": 1.5, "hook_energy": 0.4}},
    )
    good_feedback = SimpleNamespace(
        action=SimpleNamespace(value="upvote"), created_at=datetime.now(UTC)
    )
    good_outcome = SimpleNamespace(performed_well=True)

    skipped_clip = SimpleNamespace(id=uuid.uuid4(), dna_match=None, score=None, signals_jsonb=None)
    skipped_feedback = SimpleNamespace(
        action=SimpleNamespace(value="skip"), created_at=datetime.now(UTC)
    )

    result = MagicMock()
    result.all.return_value = [
        (good_feedback, good_clip, good_outcome),
        (skipped_feedback, skipped_clip, SimpleNamespace(performed_well=True)),
    ]
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)

    labeled = await load_labeled_clips(session, creator_id)

    assert len(labeled) == 1, "skip + performed_well must NOT enter the eval set"
    lc = labeled[0]
    assert lc.clip_id == good_clip.id
    assert lc.performed_well is True
    assert lc.dna_composite == pytest.approx(0.7)
    assert lc.action == "upvote"
