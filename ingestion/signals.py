"""
Unified signal timeline builder.

Merges audio events (energy_spikes / silence / laughter) with retention-curve
spikes into a single chronologically-sorted list stored in signals.timeline_jsonb.
"""

_RETENTION_SPIKE_THRESHOLD = 1.2  # relative_retention_performance above this is a spike


def build_signal_timeline(audio_events: dict, retention_points: list) -> dict:
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
        if rrp is not None and rrp > _RETENTION_SPIKE_THRESHOLD:
            events.append(
                {
                    "type": "retention_spike",
                    "start_s": getattr(pt, "timestamp_s", 0.0),
                    "audience_watch_ratio": getattr(pt, "audience_watch_ratio", 0.0),
                    "relative_retention": rrp,
                }
            )

    events.sort(key=lambda e: e.get("start_s", 0.0))

    return {
        "version": 1,
        "duration_s": audio_events.get("duration_s", 0.0),
        "events": events,
    }
