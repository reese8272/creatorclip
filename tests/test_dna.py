"""
Unit tests for dna/builder.py, dna/brief.py, dna/profile.py.

Pure-function and mock-at-boundary tests — no DB, no network.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from dna.brief import _DISCLAIMER, generate_brief
from dna.builder import (
    _best_source_region,
    _hook_text,
    _optimal_clip_len_s,
    _optimal_upload_gap_h,
    _recency_weight,
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


# ── _optimal_upload_gap_h ──────────────────────────────────────────────────────


def _make_activity(day: int, hour: int, idx: float):
    return SimpleNamespace(day_of_week=day, hour=hour, activity_index=idx)


def test_optimal_upload_gap_h_basic():
    rows = [
        _make_activity(0, 8, 0.9),  # hour_of_week = 8
        _make_activity(0, 12, 0.95),  # hour_of_week = 12
        _make_activity(0, 20, 0.7),  # hour_of_week = 20
    ]
    gap = _optimal_upload_gap_h(rows)
    # top 3: 12, 8, 20 → sorted [8, 12, 20] → gaps [4, 8] → avg 6.0
    assert gap == pytest.approx(6.0)


def test_optimal_upload_gap_h_none_on_single_row():
    rows = [_make_activity(0, 10, 0.9)]
    assert _optimal_upload_gap_h(rows) is None


def test_optimal_upload_gap_h_none_on_empty():
    assert _optimal_upload_gap_h([]) is None


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


def test_generate_brief_calls_claude_and_appends_disclaimer(monkeypatch):
    mock_response = _mock_anthropic_response("Channel insight text here.")
    with patch("dna.brief._ANTHROPIC") as mock_client:
        mock_client.messages.create.return_value = mock_response
        result = generate_brief({"top_videos": []}, "TestChannel")

    assert "Channel insight text here." in result
    assert _DISCLAIMER in result


def test_generate_brief_disclaimer_always_present(monkeypatch):
    """Honesty constraint: disclaimer must be in every brief regardless of LLM output."""
    mock_response = _mock_anthropic_response("Some brief content.")
    with patch("dna.brief._ANTHROPIC") as mock_client:
        mock_client.messages.create.return_value = mock_response
        result = generate_brief({}, "AnyChannel")

    assert "does not promise virality" in result


def test_generate_brief_raises_on_empty_response():
    resp = MagicMock()
    resp.content = []
    resp.usage = MagicMock(input_tokens=0, output_tokens=0)
    with patch("dna.brief._ANTHROPIC") as mock_client:
        mock_client.messages.create.return_value = resp
        with pytest.raises(RuntimeError, match="no text block"):
            generate_brief({}, "BadChannel")


def test_generate_brief_uses_prompt_caching():
    """The system block must carry cache_control so the corpus is cached."""
    mock_response = _mock_anthropic_response("Brief here.")
    with patch("dna.brief._ANTHROPIC") as mock_client:
        mock_client.messages.create.return_value = mock_response
        generate_brief({"top_videos": []}, "TestChannel")

    call_kwargs = mock_client.messages.create.call_args.kwargs
    system = call_kwargs.get("system", [])
    assert len(system) == 1
    assert system[0].get("cache_control") == {"type": "ephemeral"}
