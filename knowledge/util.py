"""Shared utilities for the knowledge module."""

import json


def wrap_untrusted(name: str, value: str) -> str:
    """Wrap attacker-influenceable text so Claude cannot break out of its bounds.

    The XML label makes provenance explicit; JSON-encoding the value prevents
    quote/bracket break-out.  Per Anthropic 'Mitigate jailbreaks and prompt
    injections' (2026-06-23) and OWASP LLM01:2025: untrusted content must never
    go in the system role, and JSON-encoding is the specified defense against
    quote-break-out for non-tool-use flows.

    Usage: prepend the returned string to the user-turn message content before
    any instruction text, so the model sees untrusted content arrive from the
    user role with an explicit structural delimiter.

    Args:
        name: A short, machine-readable label describing the provenance of the
              value (e.g. ``'creator_stated_identity'``, ``'video_title'``).
        value: The raw, attacker-influenceable string.  Any characters
               (quotes, angle brackets, multi-byte) are safe — json.dumps
               normalizes them.

    Returns:
        A string of the form:
            <untrusted name="creator_stated_identity">"json-encoded value"</untrusted>
    """
    return f'<untrusted name="{name}">{json.dumps(value)}</untrusted>\n'


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
