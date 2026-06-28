"""
Unified signal timeline builder.

Merges audio events (energy_spikes / silence / laughter) with retention-curve
spikes into a single chronologically-sorted list stored in signals.timeline_jsonb.
"""

import logging
import math
from collections.abc import Sequence
from typing import Any

logger = logging.getLogger(__name__)

# Duck-typed retention-curve row contract — both ``RetentionCurve`` ORM rows
# and plain dicts/objects exposing the same attributes work, as long as they
# carry ``timestamp_s: float``, ``audience_watch_ratio: float``,
# ``relative_retention_performance: float | None``. Typed loosely as
# ``Sequence[Any]`` because SQLAlchemy ``Mapped[T]`` descriptors don't
# satisfy a structural Protocol under mypy. (Issue 108)

_RETENTION_SPIKE_THRESHOLD = 1.2  # relative_retention_performance above this is a spike


def _event_geometry_is_valid(event: dict, duration_s: float) -> bool:
    """Reject events whose timestamps are malformed (Issue 327).

    The downstream signal/clip layer (``window.py``, ``candidates.py``,
    ``scoring.py``) only compares timestamps against *positive* thresholds, so an
    inverted (``end_s < start_s``), negative, non-finite, or out-of-bounds event
    used to pass through silently and distort the signal array or anchor a clip's
    setup to the wrong point. Validate once at this build boundary instead.

    Rules:
      * ``start_s`` must be a finite number ``>= 0``.
      * if ``end_s`` is present it must be a finite number ``>= start_s``
        (point events like ``retention_spike`` carry no ``end_s`` — that's fine).
      * when the video duration is known (> 0), ``start_s`` must be ``<= duration_s``.
    """
    start = event.get("start_s")
    if not isinstance(start, (int, float)) or isinstance(start, bool):
        return False
    if not math.isfinite(start) or start < 0:
        return False

    if "end_s" in event:
        end = event.get("end_s")
        if not isinstance(end, (int, float)) or isinstance(end, bool):
            return False
        if not math.isfinite(end) or end < start:
            return False

    return not (duration_s > 0 and start > duration_s)


def build_signal_timeline(
    audio_events: dict[str, Any],
    retention_points: Sequence[Any],
) -> dict:
    """
    audio_events: output of ingestion.audio.extract_audio_events()
    retention_points: list of RetentionCurve ORM rows (or dicts with same fields)
    """
    duration_s = float(audio_events.get("duration_s", 0.0) or 0.0)
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

    # Drop malformed events once, at the build boundary (Issue 327), so the
    # downstream signal/clip layer never has to defend against inverted/negative/
    # out-of-bounds timestamps. A dropped event is logged with a count — never
    # silently swallowed — so bad upstream data is visible in production.
    clean: list[dict] = []
    dropped = 0
    for ev in events:
        if _event_geometry_is_valid(ev, duration_s):
            clean.append(ev)
        else:
            dropped += 1
    if dropped:
        logger.warning(
            "build_signal_timeline dropped %d malformed event(s) with "
            "inverted/negative/out-of-bounds timestamps; kept %d (duration_s=%.3f)",
            dropped,
            len(clean),
            duration_s,
        )
    events = clean

    events.sort(key=lambda e: e.get("start_s", 0.0))

    return {
        "version": 1,
        "duration_s": duration_s,
        "events": events,
    }
