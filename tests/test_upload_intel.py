"""
Unit tests for upload_intel/timing.py and improvement/brief.py.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from improvement.brief import _DISCLAIMER, generate_improvement_brief
from upload_intel.timing import best_upload_windows, optimal_gap_hours

# ── Helpers ───────────────────────────────────────────────────────────────────


def _activity(day: int, hour: int, idx: float):
    return SimpleNamespace(day_of_week=day, hour=hour, activity_index=idx)


# ── best_upload_windows ────────────────────────────────────────────────────────


def test_best_windows_returns_top_n():
    rows = [_activity(0, h, float(h)) for h in range(24)]
    result = best_upload_windows(rows, top_n=3)
    assert len(result) == 3


def test_best_windows_sorted_descending():
    rows = [_activity(0, 8, 0.5), _activity(1, 12, 0.9), _activity(2, 18, 0.3)]
    result = best_upload_windows(rows)
    assert result[0]["activity_index"] >= result[-1]["activity_index"]


def test_best_windows_empty_returns_empty():
    assert best_upload_windows([]) == []


def test_best_windows_has_required_keys():
    rows = [_activity(1, 14, 0.8)]
    result = best_upload_windows(rows)
    assert result[0]["day_name"] == "Monday"
    assert result[0]["hour"] == 14
    assert "label" in result[0]


def test_best_windows_label_pm():
    rows = [_activity(3, 15, 1.0)]
    result = best_upload_windows(rows)
    assert "PM" in result[0]["label"]


def test_best_windows_label_am():
    rows = [_activity(3, 9, 1.0)]
    result = best_upload_windows(rows)
    assert "AM" in result[0]["label"]


# ── optimal_gap_hours ─────────────────────────────────────────────────────────


def test_optimal_gap_hours_basic():
    rows = [_activity(0, 8, 0.9), _activity(0, 12, 0.95), _activity(0, 20, 0.7)]
    gap = optimal_gap_hours(rows)
    # top 3 sorted by time: [8, 12, 20] → gaps [4, 8] → avg 6
    assert gap == pytest.approx(6.0)


def test_optimal_gap_hours_none_on_single():
    assert optimal_gap_hours([_activity(0, 10, 1.0)]) is None


def test_optimal_gap_hours_none_on_empty():
    assert optimal_gap_hours([]) is None


# ── Issue 103: optimal_gap_hours bounds guard ─────────────────────────────────


def test_optimal_gap_hours_skips_malformed_rows():
    """Rows with out-of-bounds day_of_week/hour are silently filtered out.
    The gap must be computed from valid rows only. (Issue 103 fix #4)
    """
    valid_a = _activity(0, 8, 0.9)  # Sunday 08:00 → slot 8
    valid_b = _activity(0, 12, 0.95)  # Sunday 12:00 → slot 12
    # Malformed row: day_of_week=7 is out of range (valid: 0–6).
    bad = _activity(7, 5, 1.0)

    gap = optimal_gap_hours([valid_a, valid_b, bad])

    # Two valid rows → top slots [8, 12] → single gap of 4 hours.
    assert gap == pytest.approx(4.0)


def test_optimal_gap_hours_returns_none_when_only_malformed():
    """If all rows are malformed, return None rather than erroring."""
    bad1 = _activity(7, 0, 0.9)
    bad2 = _activity(0, 24, 0.8)  # hour=24 is out of range
    assert optimal_gap_hours([bad1, bad2]) is None


# ── generate_improvement_brief ─────────────────────────────────────────────────


def _mock_brief_response(text: str) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    usage = MagicMock()
    usage.input_tokens = 400
    usage.output_tokens = 300
    del usage.cache_read_input_tokens
    del usage.cache_creation_input_tokens
    resp = MagicMock()
    resp.content = [block]
    resp.usage = usage
    return resp


def test_improvement_brief_disclaimer_always_present():
    mock_resp = _mock_brief_response("Here are 3 improvements.")
    with patch("improvement.brief._ANTHROPIC") as mock_client:
        mock_client.with_options.return_value.messages.create.return_value = mock_resp
        result, _usage = generate_improvement_brief("TestChannel", {})

    assert _DISCLAIMER in result
    assert "does not promise virality" in result


def test_improvement_brief_uses_web_search_tool():
    """Must pass web_search tool to Claude per the approved plan.

    Issue 84: bumped from `web_search_20250305` to `web_search_20260209`
    (GA with dynamic filtering). The default is asserted against the
    config setting directly so the test moves with the config.
    """
    from config import settings

    mock_resp = _mock_brief_response("Recommendations here.")
    with patch("improvement.brief._ANTHROPIC") as mock_client:
        mock_client.with_options.return_value.messages.create.return_value = mock_resp
        generate_improvement_brief("TestChannel", {"avg_views": 5000})

    call_kwargs = mock_client.with_options.return_value.messages.create.call_args.kwargs
    tools = call_kwargs.get("tools", [])
    tool_types = [t.get("type") for t in tools]
    assert settings.ANTHROPIC_WEB_SEARCH_TOOL in tool_types
    assert tools[0]["name"] == "web_search"


def test_improvement_brief_uses_prompt_caching():
    mock_resp = _mock_brief_response("Content here.")
    with patch("improvement.brief._ANTHROPIC") as mock_client:
        mock_client.with_options.return_value.messages.create.return_value = mock_resp
        generate_improvement_brief("TestChannel", {})

    call_kwargs = mock_client.with_options.return_value.messages.create.call_args.kwargs
    system = call_kwargs.get("system", [])
    assert system[0].get("cache_control") == {"type": "ephemeral"}


def test_improvement_brief_raises_on_empty_response():
    resp = MagicMock()
    resp.content = []
    resp.usage = MagicMock(input_tokens=0, output_tokens=0)
    with patch("improvement.brief._ANTHROPIC") as mock_client:
        mock_client.with_options.return_value.messages.create.return_value = resp
        with pytest.raises(RuntimeError, match="no text"):
            generate_improvement_brief("TestChannel", {})
