"""
Compute best upload windows from audience_activity rows.
Pure deterministic logic — no LLM.
"""

_DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


def best_upload_windows(
    activity_rows: list,
    top_n: int = 3,
) -> list[dict]:
    """
    Return the top_n audience activity windows sorted by activity_index descending.

    Each result: {day_of_week, day_name, hour, activity_index, label}
    Returns [] if no activity data is available.
    """
    if not activity_rows:
        return []

    sorted_rows = sorted(activity_rows, key=lambda r: r.activity_index, reverse=True)[:top_n]

    results = []
    for row in sorted_rows:
        dow = int(row.day_of_week)
        hour = int(row.hour)
        # Skip malformed rows rather than IndexError → 500 on the endpoint (Issue 73/75):
        # a single bad ingest must not break the whole upload-intel response.
        if not (0 <= dow <= 6) or not (0 <= hour <= 23):
            continue
        period = "AM" if hour < 12 else "PM"
        display_hour = hour if hour <= 12 else hour - 12
        display_hour = 12 if display_hour == 0 else display_hour
        results.append(
            {
                "day_of_week": dow,
                "day_name": _DAY_NAMES[dow],
                "hour": hour,
                "label": f"{_DAY_NAMES[dow]} {display_hour}:00 {period}",
                "activity_index": float(row.activity_index),
            }
        )
    return results


def optimal_gap_hours(activity_rows: list) -> float | None:
    """
    Estimate optimal hours between uploads from gap between top-3 activity peaks.
    Returns None if < 2 activity rows.
    """
    if len(activity_rows) < 2:
        return None

    # Validate and coerce before any arithmetic — mirrors the hardening applied to
    # best_upload_windows in Issue 75d. A single malformed row must not raise
    # AttributeError or produce a nonsense gap calculation.
    valid = [
        (int(r.day_of_week), int(r.hour), float(r.activity_index))
        for r in activity_rows
        if 0 <= int(r.day_of_week) <= 6 and 0 <= int(r.hour) <= 23
    ]
    if len(valid) < 2:
        return None

    top = sorted(valid, key=lambda t: t[2], reverse=True)[:3]
    times = sorted(dow * 24 + hour for dow, hour, _ in top)
    if len(times) < 2:
        return None
    gaps = [times[i + 1] - times[i] for i in range(len(times) - 1)]
    return sum(gaps) / len(gaps)
