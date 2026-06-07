"""
Unified signal timeline builder.

Merges audio events (energy_spikes / silence / laughter) with retention-curve
spikes into a single chronologically-sorted list stored in signals.timeline_jsonb.
"""

from collections.abc import Sequence
from typing import Any

# Duck-typed retention-curve row contract — both ``RetentionCurve`` ORM rows
# and plain dicts/objects exposing the same attributes work, as long as they
# carry ``timestamp_s: float``, ``audience_watch_ratio: float``,
# ``relative_retention_performance: float | None``. Typed loosely as
# ``Sequence[Any]`` because SQLAlchemy ``Mapped[T]`` descriptors don't
# satisfy a structural Protocol under mypy. (Issue 108)

_RETENTION_SPIKE_THRESHOLD = 1.2  # relative_retention_performance above this is a spike


def build_signal_timeline(
    audio_events: dict[str, Any],
    retention_points: Sequence[Any],
) -> dict:
    """
    audio_events: output of ingestion.audio.extract_audio_events()
    retention_points: list of RetentionCurve ORM rows (or dicts with same fields)
    """
    events: list[dict] = []

    for e in audio_events.get("energy_spikes", []):
        events.append({"type": "energy_spike", **e})

    for s in audio_events.get("silences", []):
        events.append({"type": "silence", **s})

    for la in audio_events.get("laughter", []):
        events.append({"type": "laughter", **la})

    for pt in retention_points:
        rrp = getattr(pt, "relative_retention_performance", None)
        # Emit a retention_spike for two cases:
        # 1. relative_retention_performance exceeds the threshold (statistically high)
        # 2. is_rewatch_spike=True (YouTube's own "most replayed" flag) — this is
        #    ground-truth crowd signal and must fire even when rrp is unavailable or
        #    below the computed threshold. (Issue 127)
        is_rewatch = getattr(pt, "is_rewatch_spike", False)
        if (rrp is not None and rrp > _RETENTION_SPIKE_THRESHOLD) or is_rewatch:
            events.append(
                {
                    "type": "retention_spike",
                    "start_s": getattr(pt, "timestamp_s", 0.0),
                    "audience_watch_ratio": getattr(pt, "audience_watch_ratio", 0.0),
                    "relative_retention": rrp or 0.0,
                    "is_rewatch_spike": is_rewatch,
                }
            )

    events.sort(key=lambda e: e.get("start_s", 0.0))

    return {
        "version": 1,
        "duration_s": audio_events.get("duration_s", 0.0),
        "events": events,
    }
