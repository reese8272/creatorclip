"""
Unit tests for dna/builder.py, dna/brief.py, dna/profile.py.

Pure-function and mock-at-boundary tests — no DB, no network.
"""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dna.brief import _DISCLAIMER, generate_brief
from dna.builder import (
    _best_source_region,
    _hook_text,
    _optimal_clip_len_s,
    _recency_weight,
    optimal_gap_hours,
)

# ── _recency_weight ────────────────────────────────────────────────────────────


def test_recency_weight_today_is_near_one():
    now = datetime.now(UTC)
    w = _recency_weight(now)
    assert w == pytest.approx(1.0, abs=0.01)


def test_recency_weight_ninety_days_is_half():
    ninety_ago = datetime.now(UTC) - timedelta(days=90)
    w = _recency_weight(ninety_ago)
    assert w == pytest.approx(0.5, abs=0.01)


def test_recency_weight_one_eighty_days_is_quarter():
    old = datetime.now(UTC) - timedelta(days=180)
    w = _recency_weight(old)
    assert w == pytest.approx(0.25, abs=0.01)


def test_recency_weight_none_returns_moderate():
    w = _recency_weight(None)
    assert 0.0 < w < 1.0


def test_recency_weight_naive_datetime_handled():
    naive = datetime.now() - timedelta(days=30)
    w = _recency_weight(naive)
    assert 0.0 < w < 1.0


def test_recency_weight_decreases_with_age():
    recent = datetime.now(UTC) - timedelta(days=10)
    old = datetime.now(UTC) - timedelta(days=200)
    assert _recency_weight(recent) > _recency_weight(old)


# ── _hook_text ─────────────────────────────────────────────────────────────────


def test_hook_text_extracts_first_words():
    segments = {"segments": [{"words": [{"word": w} for w in ["Hello", "world", "this", "is"]]}]}
    result = _hook_text(segments)
    assert result == "Hello world this is"


def test_hook_text_empty_segments():
    assert _hook_text({"segments": []}) == ""


def test_hook_text_respects_word_limit():
    words = [{"word": f"w{i}"} for i in range(100)]
    segments = {"segments": [{"words": words}]}
    result = _hook_text(segments)
    assert len(result.split()) == 40  # _HOOK_WORDS cap


def test_hook_text_spans_multiple_segments():
    seg1 = {"words": [{"word": "First"}]}
    seg2 = {"words": [{"word": "Second"}]}
    result = _hook_text({"segments": [seg1, seg2]})
    assert "First" in result and "Second" in result


# ── _best_source_region ────────────────────────────────────────────────────────


def _make_ret(ts: float, ratio: float, is_rewatch: bool = False):
    return SimpleNamespace(timestamp_s=ts, audience_watch_ratio=ratio, is_rewatch_spike=is_rewatch)


def test_best_source_region_first_third_wins():
    rows = [
        _make_ret(5.0, 0.95),  # first_third
        _make_ret(6.0, 0.93),  # first_third
        _make_ret(40.0, 0.60),  # middle
        _make_ret(80.0, 0.30),  # last_third
    ]
    assert _best_source_region(rows, 100.0) == "first_third"


def test_best_source_region_middle_wins():
    rows = [
        _make_ret(5.0, 0.50),
        _make_ret(45.0, 0.90),
        _make_ret(46.0, 0.88),
        _make_ret(90.0, 0.20),
    ]
    assert _best_source_region(rows, 100.0) == "middle"


def test_best_source_region_none_on_empty():
    assert _best_source_region([], 100.0) is None


def test_best_source_region_none_on_zero_duration():
    rows = [_make_ret(5.0, 0.9)]
    assert _best_source_region(rows, 0.0) is None


# ── _optimal_clip_len_s ────────────────────────────────────────────────────────


def test_optimal_clip_len_odd_count():
    videos = [{"avg_view_duration_s": v} for v in [30.0, 45.0, 60.0]]
    assert _optimal_clip_len_s(videos) == pytest.approx(45.0)


def test_optimal_clip_len_even_count():
    videos = [{"avg_view_duration_s": v} for v in [20.0, 40.0, 60.0, 80.0]]
    assert _optimal_clip_len_s(videos) == pytest.approx(50.0)


def test_optimal_clip_len_none_on_empty():
    assert _optimal_clip_len_s([]) is None


def test_optimal_clip_len_skips_none_values():
    videos = [{"avg_view_duration_s": None}, {"avg_view_duration_s": 50.0}]
    assert _optimal_clip_len_s(videos) == pytest.approx(50.0)


# ── upload-gap delegation (2026-07-20 assessment) ─────────────────────────────
# The builder's former `_optimal_upload_gap_h` near-duplicated
# `upload_intel.timing.optimal_gap_hours` without its circular-week wrap or
# malformed-row guard. It now delegates; the full behavior (wrap, bounds,
# malformed rows) is covered by tests/test_upload_intel.py. This pins the
# delegation itself: the imported function applies the circular-week wrap.


def _make_activity(day: int, hour: int, idx: float):
    return SimpleNamespace(day_of_week=day, hour=hour, activity_index=idx)


def test_builder_upload_gap_uses_circular_week_wrap():
    # Saturday 23:00 (slot 167) vs Monday 01:00 (slot 25): shorter arc is 26 h,
    # not 142 h — the exact defect the duplicated helper reintroduced.
    rows = [_make_activity(6, 23, 0.9), _make_activity(1, 1, 0.95)]
    assert optimal_gap_hours(rows) == pytest.approx(26.0)


# ── generate_brief ─────────────────────────────────────────────────────────────


def _mock_anthropic_response(text: str) -> MagicMock:
    content_block = MagicMock()
    content_block.type = "text"
    content_block.text = text
    usage = MagicMock()
    usage.input_tokens = 500
    usage.output_tokens = 200
    del usage.cache_read_input_tokens  # ensure getattr fallback to 0
    del usage.cache_creation_input_tokens
    resp = MagicMock()
    resp.content = [content_block]
    resp.usage = usage
    return resp


async def test_generate_brief_calls_claude_and_appends_disclaimer(monkeypatch):
    mock_response = _mock_anthropic_response("Channel insight text here.")
    with patch("dna.brief._ANTHROPIC") as mock_client:
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        result, _usage = await generate_brief({"top_videos": []}, "TestChannel")

    assert "Channel insight text here." in result
    assert _DISCLAIMER in result


async def test_generate_brief_disclaimer_always_present(monkeypatch):
    """Honesty constraint: disclaimer must be in every brief regardless of LLM output."""
    mock_response = _mock_anthropic_response("Some brief content.")
    with patch("dna.brief._ANTHROPIC") as mock_client:
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        result, _usage = await generate_brief({}, "AnyChannel")

    assert "does not promise virality" in result


async def test_generate_brief_raises_on_empty_response():
    resp = MagicMock()
    resp.content = []
    resp.usage = MagicMock(input_tokens=0, output_tokens=0)
    with patch("dna.brief._ANTHROPIC") as mock_client:
        mock_client.messages.create = AsyncMock(return_value=resp)
        with pytest.raises(RuntimeError, match="no text block"):
            await generate_brief({}, "BadChannel")


async def test_generate_brief_no_cache_marker_below_floor():
    """Issue 315: dna/brief.py must send NO cache_control on any system block.
    The static instructions block is ~570–650 tokens — below Sonnet 4.6's
    1024-token cacheable floor. An inert marker charges the write-premium
    with zero cache reads. Dropped in Issue 315; Issue 224's trust-boundary
    structure (instructions vs volatile corpus) is retained unchanged.
    See docs/DECISIONS.md (Issues 223/224/315)."""
    mock_response = _mock_anthropic_response("Brief here.")
    with patch("dna.brief._ANTHROPIC") as mock_client:
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        await generate_brief({"top_videos": []}, "TestChannel")

    call_kwargs = mock_client.messages.create.call_args.kwargs
    system = call_kwargs.get("system", [])
    assert len(system) == 2  # static instructions + per-creator corpus
    # No cache_control on any block — prefix is below the 1024-token cacheable floor.
    assert "cache_control" not in system[0], (
        "Issue 315: dna/brief.py static block must have no cache_control — "
        "~570–650 tokens is below Sonnet 4.6's 1024-token cacheable floor."
    )
    assert "TestChannel" not in system[0]["text"]
    assert "cache_control" not in system[1]
    assert "TestChannel" in system[1]["text"]


# ── Issue 55: confirm_draft supersedes previous confirmed profile ──────────────


@pytest.mark.asyncio
async def test_confirm_draft_supersedes_previous_confirmed_profile():
    """After confirm_draft, exactly one confirmed row must exist for the creator."""
    import uuid
    from unittest.mock import AsyncMock, MagicMock

    from dna import profile
    from models import CreatorDna, DnaStatus

    creator_id = uuid.uuid4()

    # Build two in-memory CreatorDna objects: one confirmed, one draft.
    old_confirmed = CreatorDna(
        id=uuid.uuid4(),
        creator_id=creator_id,
        version=1,
        brief_text="old brief",
        patterns_jsonb={},
        top_video_ids_jsonb=[],
        bottom_video_ids_jsonb=[],
        status=DnaStatus.confirmed,
    )
    new_draft = CreatorDna(
        id=uuid.uuid4(),
        creator_id=creator_id,
        version=2,
        brief_text="new brief",
        patterns_jsonb={},
        top_video_ids_jsonb=[],
        bottom_video_ids_jsonb=[],
        status=DnaStatus.draft,
    )

    # confirm_draft now issues ONE locked select returning all of the creator's DNA
    # rows (version desc); see the real-DB coverage in
    # tests/test_dna_idempotency_integration.py. Here the single execute() returns
    # [new_draft (v2), old_confirmed (v1)].
    async def _execute(stmt):
        result = MagicMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [new_draft, old_confirmed]
        result.scalars.return_value = scalars_mock
        return result

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=_execute)
    session.flush = AsyncMock()
    session.get = AsyncMock(return_value=None)  # creator lookup → None (no onboarding update)
    session.commit = AsyncMock()
    session.refresh = AsyncMock()

    await profile.confirm_draft(session, creator_id)

    # The old confirmed row must now be superseded.
    assert old_confirmed.status == DnaStatus.superseded, (
        "Previously confirmed DNA row was not superseded"
    )
    # The draft must now be confirmed.
    assert new_draft.status == DnaStatus.confirmed, "Draft DNA row was not promoted to confirmed"


# ── Issue 98: create_draft advances onboarding_state ─────────────────────────
#
# Real-DB coverage of the full state-machine arc lives in
# `tests/test_dna_idempotency_integration.py`. These mock-based unit tests
# pin the same logic so the default (non-integration) lane catches a
# regression. Note: create_draft now issues a `session.get(Creator, ...)`
# call to read+possibly-bump the state — any prior unit test that mocked
# session without that `get` returning a creator (or returning None) is
# already compatible because the bump is gated on `creator is not None`.


def _make_creator_stub(state):
    """Tiny stand-in for a Creator row with a writable `onboarding_state`."""
    return SimpleNamespace(onboarding_state=state)


@pytest.mark.asyncio
async def test_create_draft_bumps_connected_to_dna_pending():
    """connected → dna_pending — the missing transition Issue 98 fixes."""
    import uuid
    from unittest.mock import AsyncMock, MagicMock

    from dna import profile
    from models import OnboardingState

    creator_id = uuid.uuid4()
    stub = _make_creator_stub(OnboardingState.connected)

    # session.execute returns max(version) = 0 → next draft is v1.
    exec_result = MagicMock()
    exec_result.scalar.return_value = 0

    session = AsyncMock()
    session.execute = AsyncMock(return_value=exec_result)
    session.add = MagicMock()
    session.get = AsyncMock(return_value=stub)
    session.commit = AsyncMock()
    session.refresh = AsyncMock()

    await profile.create_draft(
        session,
        creator_id=creator_id,
        patterns={},
        top_video_ids=[],
        bottom_video_ids=[],
        brief_text="v1",
    )

    assert stub.onboarding_state == OnboardingState.dna_pending, (
        "create_draft must bump connected → dna_pending (Issue 98)"
    )


@pytest.mark.asyncio
async def test_create_draft_idempotent_when_already_dna_pending():
    """A second create_draft call (e.g. a rebuild) MUST NOT churn state."""
    import uuid
    from unittest.mock import AsyncMock, MagicMock

    from dna import profile
    from models import OnboardingState

    stub = _make_creator_stub(OnboardingState.dna_pending)
    exec_result = MagicMock()
    exec_result.scalar.return_value = 1  # max_version=1 → next is v2

    session = AsyncMock()
    session.execute = AsyncMock(return_value=exec_result)
    session.add = MagicMock()
    session.get = AsyncMock(return_value=stub)
    session.commit = AsyncMock()
    session.refresh = AsyncMock()

    await profile.create_draft(
        session,
        creator_id=uuid.uuid4(),
        patterns={},
        top_video_ids=[],
        bottom_video_ids=[],
        brief_text="v2",
    )

    assert stub.onboarding_state == OnboardingState.dna_pending


@pytest.mark.asyncio
async def test_create_draft_does_not_regress_active_state():
    """Rebuild for an already-active creator must NOT downgrade to dna_pending —
    otherwise the dashboard banner would flicker back on between draft creation
    and confirmation during a v2 rebuild flow."""
    import uuid
    from unittest.mock import AsyncMock, MagicMock

    from dna import profile
    from models import OnboardingState

    stub = _make_creator_stub(OnboardingState.active)
    exec_result = MagicMock()
    exec_result.scalar.return_value = 2  # max_version=2 → next is v3

    session = AsyncMock()
    session.execute = AsyncMock(return_value=exec_result)
    session.add = MagicMock()
    session.get = AsyncMock(return_value=stub)
    session.commit = AsyncMock()
    session.refresh = AsyncMock()

    await profile.create_draft(
        session,
        creator_id=uuid.uuid4(),
        patterns={},
        top_video_ids=[],
        bottom_video_ids=[],
        brief_text="v3",
    )

    assert stub.onboarding_state == OnboardingState.active, (
        "create_draft MUST NOT regress active → dna_pending on rebuild"
    )


# ── _enrich_videos (Issue 109a split regression) ──────────────────────────────
#
# Expected output below was captured by running the PRE-SPLIT _enrich_videos
# (single ~48-line function) against this exact fixture; the split into four
# focused loaders + a thin stitch must be byte-identical.

_ENRICH_V1 = uuid.UUID(int=1)
_ENRICH_V2 = uuid.UUID(int=2)
_ENRICH_V3 = uuid.UUID(int=3)


def _enrich_result(rows):
    res = MagicMock()
    res.scalars.return_value = list(rows)
    return res


async def test_enrich_videos_matches_pre_split_output():
    from dna.builder import _enrich_videos

    transcripts = [
        SimpleNamespace(
            video_id=_ENRICH_V1,
            segments_jsonb={
                "segments": [
                    {
                        "words": [
                            {"word": w}
                            for w in [
                                "so",
                                "today",
                                "we",
                                "are",
                                "testing",
                                "the",
                                "hook",
                                "extraction",
                            ]
                        ]
                    },
                    {
                        "words": [
                            {"word": w} for w in ["and", "this", "is", "the", "second", "segment"]
                        ]
                    },
                ]
            },
        ),
    ]
    signals = [
        SimpleNamespace(
            video_id=_ENRICH_V1,
            timeline_jsonb={
                "energy_spikes": [{"start_s": 10.0}, {"start_s": 42.0}, {"start_s": 90.0}],
                "laughter": [{"start_s": 55.0}],
            },
        ),
    ]
    retention = [
        SimpleNamespace(
            video_id=_ENRICH_V1, timestamp_s=15.0, is_rewatch_spike=True, audience_watch_ratio=0.9
        ),
        SimpleNamespace(
            video_id=_ENRICH_V1, timestamp_s=150.0, is_rewatch_spike=False, audience_watch_ratio=0.5
        ),
        SimpleNamespace(
            video_id=_ENRICH_V1, timestamp_s=290.0, is_rewatch_spike=True, audience_watch_ratio=0.7
        ),
        SimpleNamespace(
            video_id=_ENRICH_V2, timestamp_s=30.0, is_rewatch_spike=False, audience_watch_ratio=0.4
        ),
        SimpleNamespace(
            video_id=_ENRICH_V2, timestamp_s=200.0, is_rewatch_spike=False, audience_watch_ratio=0.8
        ),
    ]

    session = MagicMock()
    # Query order is part of the contract: Transcript, Signals, RetentionCurve —
    # exactly 3 batched IN-queries (no N+1), same as pre-split.
    session.execute = AsyncMock(
        side_effect=[
            _enrich_result(transcripts),
            _enrich_result(signals),
            _enrich_result(retention),
        ]
    )
    videos = [
        {"video_id": _ENRICH_V1, "duration_s": 300.0},
        {"video_id": _ENRICH_V2, "duration_s": 240.0},
        {"video_id": _ENRICH_V3, "duration_s": None},
    ]

    await _enrich_videos(session, videos)

    assert session.execute.await_count == 3, "must stay 3 batched IN-queries (no N+1)"
    assert videos == [
        {
            "video_id": _ENRICH_V1,
            "duration_s": 300.0,
            "hook_text": (
                "so today we are testing the hook extraction and this is the second segment"
            ),
            "energy_spike_count": 3,
            "laughter_count": 1,
            "retention_spike_times": [15.0, 290.0],
            "best_source_region": "first_third",
        },
        {
            "video_id": _ENRICH_V2,
            "duration_s": 240.0,
            "hook_text": "",
            "energy_spike_count": 0,
            "laughter_count": 0,
            "retention_spike_times": [],
            "best_source_region": "last_third",
        },
        {
            "video_id": _ENRICH_V3,
            "duration_s": None,
            "hook_text": "",
            "energy_spike_count": 0,
            "laughter_count": 0,
            "retention_spike_times": [],
            "best_source_region": None,
        },
    ]


async def test_enrich_videos_empty_list_is_noop():
    from dna.builder import _enrich_videos

    session = MagicMock()
    session.execute = AsyncMock()
    await _enrich_videos(session, [])
    session.execute.assert_not_awaited()
