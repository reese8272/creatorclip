"""Shared utilities for the knowledge module."""


def extract_transcript_text(segments_jsonb: dict | None, max_chars: int) -> str:
    """Extract plain text from a transcript segments_jsonb blob.

    segments_jsonb must be a dict with a ``"segments"`` key (the canonical shape
    of ``Transcript.segments_jsonb``). Returns an empty string if missing or empty.
    """
    if not segments_jsonb:
        return ""
    segs = segments_jsonb.get("segments", [])
    parts = [seg.get("text", "").strip() for seg in segs if seg.get("text", "").strip()]
    return " ".join(parts)[:max_chars]


def extract_transcript_excerpt(
    segments_jsonb: dict | None, max_s: float, max_chars: int = 1500
) -> str:
    """Extract transcript text for segments whose start time is before max_s.

    Useful for hook analysis where only the first N seconds matter.
    """
    if not segments_jsonb:
        return ""
    segs = segments_jsonb.get("segments", [])
    parts = []
    for seg in segs:
        if float(seg.get("start", 0)) < max_s:
            text = seg.get("text", "").strip()
            if text:
                parts.append(text)
    return " ".join(parts)[:max_chars]


def get_transcript_segments(segments_jsonb: dict | None) -> list[dict]:
    """Return the raw segments list from segments_jsonb, or empty list."""
    if not segments_jsonb:
        return []
    return segments_jsonb.get("segments", [])
