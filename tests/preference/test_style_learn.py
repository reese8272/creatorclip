"""Issue 187 — Style-learning unit tests.

Tests:
  - dominant_style: returns None when history is empty
  - dominant_style: returns None when count is below threshold
  - dominant_style: returns the value when count meets the threshold
  - dominant_style: skips entries where the field is absent
  - dominant_style: handles boolean values (captions_enabled, etc.)
  - style_suggestion: returns None for an empty history (cold-start safe)
  - style_suggestion: returns None when no field meets the threshold
  - style_suggestion: returns the first dominant field in _KIT_FIELDS order
  - style_suggestion: respects a custom threshold
  - style_suggestion: count in the returned dict equals the actual occurrence count
"""

from preference.style_learn import dominant_style, style_suggestion

# ── dominant_style ────────────────────────────────────────────────────────────


def test_dominant_style_empty_history_returns_none() -> None:
    assert dominant_style([], "subtitle") is None


def test_dominant_style_below_threshold_returns_none() -> None:
    history = [{"subtitle": "bold_pop"}] * 4  # 4 < threshold=5
    assert dominant_style(history, "subtitle", threshold=5) is None


def test_dominant_style_meets_threshold_returns_value() -> None:
    history = [{"subtitle": "bold_pop"}] * 5
    assert dominant_style(history, "subtitle", threshold=5) == "bold_pop"


def test_dominant_style_exceeds_threshold_returns_value() -> None:
    history = [{"subtitle": "minimal"}] * 8
    assert dominant_style(history, "subtitle", threshold=5) == "minimal"


def test_dominant_style_skips_missing_field() -> None:
    # 4 rows have the field, 4 rows don't — only 4 actual occurrences
    history = [{"subtitle": "bold_pop"}] * 4 + [{"background": "blur"}] * 4
    assert dominant_style(history, "subtitle", threshold=5) is None


def test_dominant_style_boolean_value() -> None:
    history = [{"captions_enabled": True}] * 6
    assert dominant_style(history, "captions_enabled", threshold=5) is True


def test_dominant_style_false_boolean_not_skipped() -> None:
    """False is a valid dominant value — must not be treated as absent."""
    history = [{"zoom_on_peak": False}] * 6
    result = dominant_style(history, "zoom_on_peak", threshold=5)
    assert result is False


# ── style_suggestion ──────────────────────────────────────────────────────────


def test_style_suggestion_empty_history_returns_none() -> None:
    assert style_suggestion([]) is None


def test_style_suggestion_sparse_history_returns_none() -> None:
    """With < threshold occurrences of any value, no suggestion is made."""
    history = [{"subtitle": "bold_pop"}] * 3 + [{"background": "blur"}] * 3
    assert style_suggestion(history, threshold=5) is None


def test_style_suggestion_returns_first_dominant_field() -> None:
    """subtitle comes before background in _KIT_FIELDS — should be returned first."""
    history = [{"subtitle": "minimal", "background": "blur"}] * 6
    result = style_suggestion(history, threshold=5)
    assert result is not None
    assert result["field"] == "subtitle"
    assert result["value"] == "minimal"
    assert result["count"] == 6


def test_style_suggestion_returns_background_when_subtitle_absent() -> None:
    """When subtitle has no dominant, fall through to background."""
    history = [{"background": "blur"}] * 7
    result = style_suggestion(history, threshold=5)
    assert result is not None
    assert result["field"] == "background"
    assert result["value"] == "blur"
    assert result["count"] == 7


def test_style_suggestion_custom_threshold() -> None:
    history = [{"subtitle": "gradient_slide"}] * 3
    assert style_suggestion(history, threshold=5) is None
    result = style_suggestion(history, threshold=3)
    assert result is not None
    assert result["field"] == "subtitle"
    assert result["count"] == 3


def test_style_suggestion_threshold_of_one() -> None:
    """Edge: threshold=1 matches any single occurrence."""
    result = style_suggestion([{"aspect": "1:1"}], threshold=1)
    assert result is not None
    assert result["field"] == "aspect"
    assert result["value"] == "1:1"
    assert result["count"] == 1


def test_style_suggestion_returns_none_when_no_kit_field_present() -> None:
    """History rows with unrelated keys produce no suggestion."""
    history = [{"unrelated_key": "value"}] * 10
    assert style_suggestion(history, threshold=5) is None


def test_style_suggestion_count_matches_occurrences() -> None:
    """The returned count must equal the actual number of matching rows."""
    history = [{"subtitle": "bold_pop"}] * 8 + [{"subtitle": "minimal"}] * 2
    result = style_suggestion(history, threshold=5)
    assert result is not None
    assert result["field"] == "subtitle"
    assert result["value"] == "bold_pop"
    assert result["count"] == 8
