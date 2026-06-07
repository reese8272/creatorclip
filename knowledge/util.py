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
