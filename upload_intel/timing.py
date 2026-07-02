"""
Compute best upload windows from audience_activity rows.
Pure deterministic logic — no LLM.
"""

from collections.abc import Sequence
from typing import Any

# Duck-typed row contract — both ``AudienceActivity`` ORM rows and plain
# dicts/objects work, as long as they expose ``day_of_week: int``,
# ``hour: int``, ``activity_index: float``. Typed loosely as ``Sequence[Any]``
# in the signatures below because SQLAlchemy ``Mapped[T]`` descriptors don't
# satisfy a structural Protocol under mypy. (Issue 108)

_DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]

_HOURS_PER_WEEK = 168


def _coerce_row(row: Any) -> tuple[int, int, float] | None:
    """Validate + coerce one activity row to ``(day_of_week, hour, activity_index)``.

    Returns None for malformed rows (out-of-range or non-coercible values) so a
    single bad ingest never breaks the whole upload-intel response (Issue 73/75).
    Shared by both public functions so they filter with identical rules.
    """
    try:
        dow = int(row.day_of_week)
        hour = int(row.hour)
        idx = float(row.activity_index)
    except (TypeError, ValueError, AttributeError):
        return None
    if not (0 <= dow <= 6) or not (0 <= hour <= 23):
        return None
    return dow, hour, idx


def best_upload_windows(
    activity_rows: Sequence[Any],
    top_n: int = 3,
) -> list[dict]:
    """
    Return the top_n audience activity windows sorted by activity_index descending.

    Each result: {day_of_week, day_name, hour, activity_index, label}
    Returns [] if no activity data is available.
    """
    if not activity_rows:
        return []

    # Filter+coerce BEFORE slicing — slicing first would let a malformed row
    # occupy a top-N slot and under-fill the result even when more valid
    # windows exist lower in the ranking. (Issue 352 Batch J)
    valid = [c for c in (_coerce_row(r) for r in activity_rows) if c is not None]
    top = sorted(valid, key=lambda t: t[2], reverse=True)[:top_n]

    results = []
    for dow, hour, idx in top:
        period = "AM" if hour < 12 else "PM"
        display_hour = hour if hour <= 12 else hour - 12
        display_hour = 12 if display_hour == 0 else display_hour
        results.append(
            {
                "day_of_week": dow,
                "day_name": _DAY_NAMES[dow],
                "hour": hour,
                "label": f"{_DAY_NAMES[dow]} {display_hour}:00 {period}",
                "activity_index": idx,
            }
        )
    return results


def optimal_gap_hours(activity_rows: Sequence[Any]) -> float | None:
    """
    Estimate optimal hours between uploads from gap between top-3 activity peaks.
    Returns None if < 2 activity rows.
    """
    if len(activity_rows) < 2:
        return None

    # Validate and coerce before any arithmetic — a single malformed row must not
    # raise or produce a nonsense gap calculation. Shared rules via _coerce_row.
    valid = [c for c in (_coerce_row(r) for r in activity_rows) if c is not None]
    if len(valid) < 2:
        return None

    top = sorted(valid, key=lambda t: t[2], reverse=True)[:3]
    times = sorted(dow * 24 + hour for dow, hour, _ in top)
    if len(times) < 2:
        return None
    # The week is circular: Saturday 23:00 (slot 167) → Sunday 00:00 (slot 0) is a
    # 1-hour gap, not 167. Use the shorter arc of the 168-hour week. (Issue 352 Batch J)
    gaps = [
        min(times[i + 1] - times[i], _HOURS_PER_WEEK - (times[i + 1] - times[i]))
        for i in range(len(times) - 1)
    ]
    return sum(gaps) / len(gaps)
