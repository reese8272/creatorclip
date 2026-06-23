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
    """The per-creator DNA block carries a 1h cache breakpoint; static block leads, uncached.

    Stable-content-first ordering + extended TTL so a creator's videos scored within an hour
    reuse the cached DNA prefix instead of re-billing it at full price. (Issue 78b)
    """
    candidates = [_candidate()]
    mock_resp = _mock_claude_response(
        [{"index": 0, "score": 0.7, "principle": "Loop-ability", "reasoning": "Clean loop."}]
    )
    with patch("clip_engine.scoring._ANTHROPIC") as mock_client:
        mock_client.messages.create = AsyncMock(return_value=mock_resp)
        await score_candidates(candidates, _timeline(), dna_brief="some dna")

    system = mock_client.messages.create.call_args.kwargs.get("system", [])
    assert len(system) == 2
    # Static instructions lead and are NOT the cache breakpoint (stable bytes first).
    assert "cache_control" not in system[0]
    assert "NAMED SCORING PRINCIPLES" in system[0]["text"]
    assert "some dna" not in system[0]["text"]
    # The per-creator DNA is the last block and carries the 1h breakpoint.
    assert system[-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert "some dna" in system[-1]["text"]


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
