"""
Unit tests for clip_engine/scoring.py and clip_engine/ranking.py.

Claude calls are patched at the AsyncAnthropic boundary.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clip_engine.ranking import rank_candidates
from clip_engine.scoring import (
    _signal_score,
    _transcript_context,
    compute_features,
    score_candidates,
)

# ── Fixtures ───────────────────────────────────────────────────────────────────


def _timeline(events=None, duration_s=200.0):
    return {"duration_s": duration_s, "events": events or []}


def _candidate(setup=10.0, peak=60.0, end=80.0):
    return {"setup_start_s": setup, "start_s": 0.0, "peak_s": peak, "end_s": end}


def _mock_claude_response(scores: list[dict]) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = json.dumps(scores)
    usage = MagicMock()
    usage.input_tokens = 300
    usage.output_tokens = 150
    del usage.cache_read_input_tokens
    del usage.cache_creation_input_tokens
    resp = MagicMock()
    resp.content = [block]
    resp.usage = usage
    return resp


# ── compute_features ──────────────────────────────────────────────────────────


def test_compute_features_keys():
    feats = compute_features(_candidate(), _timeline())
    for key in (
        "signal_density",
        "hook_energy",
        "silence_ratio",
        "has_retention_spike",
        "has_laughter",
        "clip_duration_s",
        "setup_length_s",
    ):
        assert key in feats


def test_compute_features_empty_timeline():
    feats = compute_features(_candidate(), {"duration_s": 0.0, "events": []})
    assert feats["signal_density"] == 0.0
    assert feats["clip_duration_s"] == 0.0


def test_compute_features_detects_retention_spike():
    tl = _timeline([{"type": "retention_spike", "start_s": 55.0, "end_s": 57.0, "value": 1.5}])
    feats = compute_features(_candidate(setup=40.0, peak=60.0, end=80.0), tl)
    assert feats["has_retention_spike"] is True


def test_compute_features_no_spike_outside_window():
    tl = _timeline([{"type": "retention_spike", "start_s": 5.0, "end_s": 6.0, "value": 1.5}])
    feats = compute_features(_candidate(setup=40.0, peak=60.0, end=80.0), tl)
    assert feats["has_retention_spike"] is False


def test_compute_features_laughter_detected():
    tl = _timeline([{"type": "laughter", "start_s": 50.0, "end_s": 52.0, "value": 0.7}])
    feats = compute_features(_candidate(setup=40.0, peak=60.0, end=80.0), tl)
    assert feats["has_laughter"] is True


def test_compute_features_event_on_lower_window_edge_is_in_window():
    """An event whose start_s == setup_s (lower edge) is INSIDE the window.

    Mutation guard (Issue 273): pins the lower `<=` in
    `_in_window` (setup_s <= start_s <= end_s). A mutant flipping it to `<`
    would silently drop a setup-aligned retention spike — the engine clips the
    SETUP, so an event landing exactly at the setup boundary must count.
    """
    tl = _timeline([{"type": "retention_spike", "start_s": 40.0, "end_s": 41.0, "value": 1.5}])
    feats = compute_features(_candidate(setup=40.0, peak=60.0, end=80.0), tl)
    assert feats["has_retention_spike"] is True


def test_compute_features_event_on_upper_window_edge_is_in_window():
    """An event whose start_s == end_s (upper edge) is INSIDE the window.

    Mutation guard (Issue 273): pins the upper `<=` in
    `_in_window`. A mutant flipping it to `<` would drop an event landing
    exactly on the window's closing boundary.
    """
    tl = _timeline([{"type": "retention_spike", "start_s": 80.0, "end_s": 81.0, "value": 1.5}])
    feats = compute_features(_candidate(setup=40.0, peak=60.0, end=80.0), tl)
    assert feats["has_retention_spike"] is True


def test_compute_features_numeric_values_exact():
    """Pin the numeric feature outputs, not just their keys.

    Mutation guard (Issue 273): the key-only test let mutants survive that
    flipped clip_duration (end_s - setup_s), setup_length (peak_s - setup_s),
    and the silence_ratio quotient. These assertions kill those mutants by
    asserting the exact arithmetic.
    """
    silence = {"type": "silence", "start_s": 45.0, "end_s": 50.0}  # 5s of silence, in-window
    feats = compute_features(_candidate(setup=40.0, peak=60.0, end=80.0), _timeline([silence]))
    # clip_duration = end_s - setup_s = 80 - 40
    assert feats["clip_duration_s"] == pytest.approx(40.0)
    # setup_length = peak_s - setup_s = 60 - 40
    assert feats["setup_length_s"] == pytest.approx(20.0)
    # silence_ratio = silence_duration / clip_dur = 5 / 40
    assert feats["silence_ratio"] == pytest.approx(5.0 / 40.0)


def test_compute_features_silence_outside_window_excluded():
    """Silence starting before the setup edge is NOT counted in silence_ratio.

    Mutation guard (Issue 273): pins that _in_window gates silence too — a
    mutant widening the window would pull in out-of-window silence.
    """
    silence = {"type": "silence", "start_s": 5.0, "end_s": 35.0}  # before setup=40
    feats = compute_features(_candidate(setup=40.0, peak=60.0, end=80.0), _timeline([silence]))
    assert feats["silence_ratio"] == pytest.approx(0.0)


# ── _signal_score ─────────────────────────────────────────────────────────────


def test_signal_score_in_range():
    feats = {
        "signal_density": 2.0,
        "hook_energy": 1.0,
        "silence_ratio": 0.1,
        "has_retention_spike": False,
        "has_laughter": False,
    }
    score = _signal_score(feats)
    assert 0.0 <= score <= 1.0


def test_signal_score_spike_raises_score():
    base = {
        "signal_density": 1.0,
        "hook_energy": 1.0,
        "silence_ratio": 0.0,
        "has_retention_spike": False,
        "has_laughter": False,
    }
    with_spike = {**base, "has_retention_spike": True}
    assert _signal_score(with_spike) > _signal_score(base)


def test_signal_score_never_exceeds_one():
    feats = {
        "signal_density": 999.0,
        "hook_energy": 999.0,
        "silence_ratio": 0.0,
        "has_retention_spike": True,
        "has_laughter": True,
    }
    assert _signal_score(feats) <= 1.0


# ── _transcript_context ────────────────────────────────────────────────────────


def test_transcript_context_three_sections():
    """Issue 224: transcript sections are now wrapped via wrap_untrusted to prevent
    transcript content from spoofing the section labels. The old [BEFORE]/[CLIP]/[AFTER]
    labels are replaced by XML-labeled JSON-encoded wrappers."""
    segs = [
        {"start": 0.0, "end": 5.0, "text": "lead in text"},  # before
        {"start": 10.0, "end": 15.0, "text": "the clip here"},  # clip
        {"start": 20.0, "end": 25.0, "text": "payoff after"},  # after
    ]
    result = _transcript_context(10.0, 20.0, segs)
    # Issue 224: sections are now wrap_untrusted wrappers, not raw [LABEL] strings.
    assert 'name="transcript_before"' in result
    assert "lead in text" in result
    assert 'name="transcript_clip"' in result
    assert "the clip here" in result
    assert 'name="transcript_after"' in result
    assert "payoff after" in result


def test_transcript_context_clip_window_only():
    """Only segments inside the clip window; no before/after → only clip section."""
    segs = [{"start": 10.0, "end": 15.0, "text": "only in clip"}]
    result = _transcript_context(10.0, 20.0, segs)
    assert 'name="transcript_clip"' in result
    assert "only in clip" in result
    assert 'name="transcript_before"' not in result
    assert 'name="transcript_after"' not in result


def test_transcript_context_empty_segments():
    assert _transcript_context(10.0, 20.0, []) == ""
    assert _transcript_context(10.0, 20.0, None) == ""


def test_transcript_context_before_excludes_clip_text():
    """The before-section must not contain text from the clip window."""
    segs = [
        {"start": 5.0, "end": 8.0, "text": "before only"},
        {"start": 12.0, "end": 18.0, "text": "clip only"},
    ]
    result = _transcript_context(10.0, 20.0, segs)
    # Split on the clip section wrapper to isolate the before section.
    before_section = (
        result.split('<untrusted name="transcript_clip">')[0]
        if 'name="transcript_clip"' in result
        else result
    )
    assert "before only" in before_section
    assert "clip only" not in before_section


# ── score_candidates cold-start ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_score_candidates_cold_start_no_llm():
    """Cold-start path must not call Claude at all."""
    candidates = [_candidate()]
    with patch("clip_engine.scoring._ANTHROPIC") as mock_client:
        result = await score_candidates(candidates, _timeline(), dna_brief=None)

    mock_client.messages.create.assert_not_called()
    assert len(result) == 1
    assert 0.0 <= result[0]["score"] <= 1.0
    assert result[0]["principle"] == "Retention curve is ground truth"


@pytest.mark.asyncio
async def test_score_candidates_empty_returns_empty():
    result = await score_candidates([], _timeline())
    assert result == []


# ── score_candidates DNA path ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_score_candidates_with_dna_calls_claude():
    candidates = [_candidate(setup=10.0, peak=60.0, end=80.0)]
    claude_scores = [
        {
            "index": 0,
            "score": 0.85,
            "principle": "Hook in the first 3 seconds",
            "reasoning": "Strong hook energy.",
        }
    ]
    mock_resp = _mock_claude_response(claude_scores)

    with patch("clip_engine.scoring._ANTHROPIC") as mock_client:
        mock_client.messages.create = AsyncMock(return_value=mock_resp)
        result = await score_candidates(candidates, _timeline(), dna_brief="DNA brief text")

    mock_client.messages.create.assert_called_once()
    assert result[0]["score"] == pytest.approx(0.85)
    assert result[0]["principle"] == "Hook in the first 3 seconds"


@pytest.mark.asyncio
async def test_score_candidates_dna_uses_prompt_caching():
    """cache_control (ttl:1h) is set on the DNA block when the combined prefix clears
    Sonnet 4.6's 1024-token floor (≥ 4096 chars); omitted for short prefixes so we
    never pay the 2× write premium on an inert marker. (Issue 78b; floor fix Issue 315)

    Measured block1 static text: ~2690 chars (~670 tokens). A 1500-char DNA brief
    brings the combined prefix to ~4190 chars (≥ 4096 threshold) → marker is set.
    A 10-char brief stays below the threshold → marker is absent.

    Token-count method: char/4 (conservative lower bound). If chars/4 ≥ 1024 the
    actual BPE token count is almost certainly ≥ 1024. (Issue 315)
    """
    from clip_engine.scoring import _CACHE_FLOOR_CHARS, _PRINCIPLES, _SYSTEM_STATIC

    static_text = _SYSTEM_STATIC.format(principles="\n".join(f"- {p}" for p in _PRINCIPLES))

    # --- case 1: long DNA brief clears the floor → marker expected ---
    long_dna = "X" * (_CACHE_FLOOR_CHARS - len(static_text) - len("CREATOR DNA:\n") + 10)
    candidates = [_candidate()]
    mock_resp = _mock_claude_response(
        [{"index": 0, "score": 0.7, "principle": "Loop-ability", "reasoning": "Clean loop."}]
    )
    with patch("clip_engine.scoring._ANTHROPIC") as mock_client:
        mock_client.messages.create = AsyncMock(return_value=mock_resp)
        await score_candidates(candidates, _timeline(), dna_brief=long_dna)

    system = mock_client.messages.create.call_args.kwargs.get("system", [])
    assert len(system) == 2
    # Block 0: static instructions + rubric (stable, creator-agnostic, never the breakpoint).
    assert "cache_control" not in system[0]
    assert "NAMED SCORING PRINCIPLES" in system[0]["text"]
    assert long_dna[:10] not in system[0]["text"]
    # Block 1: DNA block carries the 1h breakpoint when prefix clears the floor.
    assert system[1].get("cache_control") == {"type": "ephemeral", "ttl": "1h"}, (
        "Issue 315: cache_control must be set on the DNA block when combined prefix "
        "chars // 4 >= 1024 (floor cleared). Measured block1 chars: "
        f"{len(static_text)}, DNA block chars: {len('CREATOR DNA:\\n') + len(long_dna)}."
    )
    assert long_dna[:10] in system[1]["text"]

    # --- case 2: short DNA brief is below floor → no marker, no 2× premium ---
    short_dna = "brief"
    with patch("clip_engine.scoring._ANTHROPIC") as mock_client2:
        mock_client2.messages.create = AsyncMock(return_value=mock_resp)
        await score_candidates(candidates, _timeline(), dna_brief=short_dna)

    system2 = mock_client2.messages.create.call_args.kwargs.get("system", [])
    assert len(system2) == 2
    assert "cache_control" not in system2[1], (
        "Issue 315: cache_control must NOT be set when the combined prefix chars // 4 < 1024. "
        "An inert marker charges the 2× write premium with zero cache reads."
    )


@pytest.mark.asyncio
async def test_score_candidates_falls_back_on_bad_json():
    """Non-JSON response must fall back to signal score gracefully."""
    candidates = [_candidate()]
    block = MagicMock()
    block.type = "text"
    block.text = "Not valid JSON at all"
    resp = MagicMock()
    resp.content = [block]
    resp.usage = MagicMock(input_tokens=10, output_tokens=5)
    del resp.usage.cache_read_input_tokens
    del resp.usage.cache_creation_input_tokens

    with patch("clip_engine.scoring._ANTHROPIC") as mock_client:
        mock_client.messages.create = AsyncMock(return_value=resp)
        result = await score_candidates(candidates, _timeline(), dna_brief="dna")

    assert 0.0 <= result[0]["score"] <= 1.0  # signal fallback


# ── Issue 315: cache floor + cost ledger correctness ─────────────────────────


@pytest.mark.asyncio
async def test_score_candidates_no_2x_write_premium_when_marker_absent():
    """When the DNA brief is too short to clear the 1024-token cache floor,
    cache_control must not be sent AND _estimate_cost_usd must not be called
    with cache_write_multiplier=2.0 (the 1h-TTL premium).

    Short brief → combined prefix < 4096 chars → prefix_clears_floor=False →
    marker absent → default multiplier (settings.COST_CACHE_WRITE_MULTIPLIER = 1.25×).
    (Issue 315: inert marker was charging 2× premium with zero cache reads.)
    """
    import uuid
    from unittest.mock import AsyncMock, MagicMock, patch

    candidates = [_candidate()]
    mock_resp = _mock_claude_response(
        [{"index": 0, "score": 0.6, "principle": "Hook in the first 3 seconds", "reasoning": "x"}]
    )

    creator_id = uuid.uuid4()
    # Issue 82b: the ledger write now opens its own short-lived session from a
    # factory AFTER the LLM call, so callers never hold a connection across it.
    mock_session = MagicMock()
    mock_session.info = {}
    mock_session.execute = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("clip_engine.scoring._ANTHROPIC") as mock_client,
        patch("clip_engine.scoring._estimate_cost_usd") as mock_cost,
        patch("clip_engine.scoring.increment_usage", new_callable=AsyncMock),
    ):
        mock_client.messages.create = AsyncMock(return_value=mock_resp)
        mock_cost.return_value = 0.001

        # Short brief — stays below the 4096-char floor threshold.
        await score_candidates(
            candidates,
            _timeline(),
            dna_brief="short",
            creator_id=creator_id,
            ledger_session_factory=lambda: mock_session,
        )

    # _estimate_cost_usd must have been called without cache_write_multiplier=2.0.
    assert mock_cost.called, (
        "cost estimator should be called when creator_id + ledger factory provided"
    )
    call_kwargs = mock_cost.call_args.kwargs
    assert call_kwargs.get("cache_write_multiplier") is None, (
        "Issue 315: cache_write_multiplier must be None (default 1.25×) when the "
        "cache marker was not sent — not 2.0 (1h-TTL premium). "
        f"Got: {call_kwargs.get('cache_write_multiplier')!r}"
    )


# ── rank_candidates ────────────────────────────────────────────────────────────


def test_rank_candidates_sorted_desc():
    candidates = [
        {"score": 0.5},
        {"score": 0.9},
        {"score": 0.3},
    ]
    ranked = rank_candidates(candidates)
    assert ranked[0]["score"] == 0.9
    assert ranked[0]["rank"] == 1
    assert ranked[-1]["rank"] == 3


def test_rank_candidates_empty():
    assert rank_candidates([]) == []


def test_rank_candidates_assigns_all_ranks():
    candidates = [{"score": 0.8}, {"score": 0.6}, {"score": 0.4}]
    ranked = rank_candidates(candidates)
    assert {c["rank"] for c in ranked} == {1, 2, 3}


# ── Issue 55: score clamping for out-of-range Claude scores ───────────────────


# ── Issue 103: dna_match ≠ composite score (collinearity fix) ────────────────


@pytest.mark.asyncio
async def test_score_candidates_separates_dna_match_from_composite():
    """DNA path: dna_match must equal the raw dna_score field from Claude, NOT the
    composite score — seeding dna_match with the composite would make it collinear
    with its own label-generating signal in the preference feature vector. (Issue 103 #5)
    """
    candidates = [_candidate(setup=10.0, peak=60.0, end=80.0)]
    # Claude returns distinct dna_score and composite score.
    claude_scores = [
        {
            "index": 0,
            "dna_score": 0.72,
            "score": 0.85,
            "principle": "Hook in the first 3 seconds",
            "reasoning": "Strong hook.",
        }
    ]
    mock_resp = _mock_claude_response(claude_scores)

    with patch("clip_engine.scoring._ANTHROPIC") as mock_client:
        mock_client.messages.create = AsyncMock(return_value=mock_resp)
        result = await score_candidates(candidates, _timeline(), dna_brief="DNA brief")

    assert result[0]["dna_match"] == pytest.approx(0.72)
    assert result[0]["score"] == pytest.approx(0.85)
    # They must be different — if equal the fix did not apply.
    assert result[0]["dna_match"] != result[0]["score"]


@pytest.mark.asyncio
async def test_score_candidates_cold_start_dna_match_is_none():
    """Cold-start path (no dna_brief): dna_match must be None so the preference
    feature vector zero-defaults it rather than using the composite signal score
    as a proxy. (Issue 103 #5)
    """
    candidates = [_candidate()]
    with patch("clip_engine.scoring._ANTHROPIC") as mock_client:
        result = await score_candidates(candidates, _timeline(), dna_brief=None)

    mock_client.messages.create.assert_not_called()
    assert result[0]["dna_match"] is None


@pytest.mark.asyncio
async def test_score_candidates_clamps_anthropic_scores_outside_unit_interval():
    """Scores returned by Claude outside [0, 1] must be clamped before returning."""
    candidates = [
        _candidate(setup=5.0, peak=30.0, end=50.0),
        _candidate(setup=60.0, peak=90.0, end=110.0),
        _candidate(setup=120.0, peak=150.0, end=170.0),
    ]
    # Claude returns out-of-range scores: 1.5, -0.3, and a valid 0.8
    claude_scores = [
        {"index": 0, "score": 1.5, "principle": "Hook in the first 3 seconds", "reasoning": "x"},
        {
            "index": 1,
            "score": -0.3,
            "principle": "Clip the setup, not the aftermath",
            "reasoning": "y",
        },
        {"index": 2, "score": 0.8, "principle": "Loop-ability", "reasoning": "z"},
    ]
    mock_resp = _mock_claude_response(claude_scores)

    with patch("clip_engine.scoring._ANTHROPIC") as mock_client:
        mock_client.messages.create = AsyncMock(return_value=mock_resp)
        result = await score_candidates(candidates, _timeline(), dna_brief="some dna brief")

    for candidate in result:
        score = candidate["score"]
        assert 0.0 <= score <= 1.0, f"score {score} is outside [0.0, 1.0] — clamping did not apply"
