"""Style-learning module — detects dominant render choices and surfaces smart defaults.

This module is pure logic (no DB dependency): it takes a list of style dicts and
returns a suggestion dict.  All I/O lives in the router; this module is fully
unit-testable without Postgres.

Algorithm: mode detection over a sliding window with a minimum-count threshold.
The threshold is config-driven (STYLE_LEARN_THRESHOLD, default 5) — the creator
must have chosen the same value at least that many times in the last N renders
before a suggestion is raised.  This matches the 'smart default' UX pattern
documented in Nielsen Norman Group default-effect literature and USPTO 10860981.

Signal source: clips.style_preset (the style actually applied at render time),
not ClipFeedback.chosen_format (a loose tag that may be absent on many rows).
Render choices are the strongest implicit-feedback signal.  See DECISIONS.md
(Issue 187).

Issue 187 — Learn the Brand Kit from repeated choices.
"""

from __future__ import annotations

from typing import Any

# Ordered list of kit fields we inspect.  Order matters: the first field whose
# count exceeds the threshold wins (avoids showing all diverging fields at once,
# which would overwhelm the UI).
_KIT_FIELDS: tuple[str, ...] = (
    "subtitle",
    "background",
    "captions_enabled",
    "zoom_on_peak",
    "denoise",
    "aspect",
)


def _dominant(history: list[dict], field: str, threshold: int) -> tuple[Any, int] | None:
    """Return ``(value, count)`` for the MOST FREQUENT value of ``field``.

    Uses the argmax over occurrence counts — not the first value to clear the
    threshold — so a newer majority choice beats an older minority one that
    happens to appear earlier in the history. (Issue 352 Batch J)

    Returns None when no value meets the threshold. Entries where the field is
    absent are skipped; ties resolve to the first-seen value.
    """
    counts: dict[Any, int] = {}
    for entry in history:
        val = entry.get(field)
        if val is None:
            continue
        counts[val] = counts.get(val, 0) + 1

    if not counts:
        return None
    val, count = max(counts.items(), key=lambda kv: kv[1])
    return (val, count) if count >= threshold else None


def dominant_style(history: list[dict], field: str, threshold: int = 5) -> Any | None:
    """Return the field value that appears >= threshold times in history.

    Args:
        history:   List of style dicts (e.g. clip.style_preset rows).
        field:     The kit key to inspect (e.g. "subtitle").
        threshold: Minimum occurrence count to qualify as dominant.

    Returns:
        The most frequent value (string/bool), or None when no value meets the
        threshold.  Entries where the field is absent are skipped.
    """
    result = _dominant(history, field, threshold)
    return result[0] if result is not None else None


def style_suggestion(
    history: list[dict],
    threshold: int = 5,
) -> dict | None:
    """Return the first kit field whose dominant value meets the threshold.

    Iterates over _KIT_FIELDS in order and returns the first match as a dict:
        {field: str, value: Any, count: int}

    Returns None when the history is sparse or no field has a clear dominant.

    Args:
        history:   List of style dicts from the last N render rows.
        threshold: Minimum occurrence count (config-driven: STYLE_LEARN_THRESHOLD).
    """
    for field in _KIT_FIELDS:
        result = _dominant(history, field, threshold)
        if result is not None:
            val, count = result
            return {"field": field, "value": val, "count": count}

    return None
