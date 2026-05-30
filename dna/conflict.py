"""Detect stated-vs-inferred conflicts so the UI can nudge the creator (Issue 83).

Per the 2026 industry-standard research (PReF + production recommender writeups):
silently overriding stated intent with engagement signals is the YouTube-algorithm
problem recreated inside our own tool. The right move is to surface the conflict
to the creator and let them decide ("update profile / keep both / split into two
styles") rather than silently re-rank by behavior.

This module is intentionally simple. It returns ``None`` if there's no conflict
worth showing, or a small dataclass with a single human-readable nudge line that
the dashboard can render in-place. The clip engine continues to use the stated
identity at full weight regardless — the conflict is a UI nudge, not a kill-switch.
"""

from dataclasses import dataclass

from models import CreatorDna, CreatorIdentity
from youtube.categories import label_for


@dataclass(frozen=True)
class IdentityDnaConflict:
    """A single nudge to show on the dashboard / profile."""

    message: str
    # Machine-readable type so the UI can pick an icon / styling. Keep this
    # small and stable — adding values is fine, renaming is not.
    kind: str  # "niche_mismatch" for now; future: "tone_mismatch", "pillar_drift"


# Free-text tokens we look for in the inferred patterns_jsonb when the creator's
# stated niche is broad. Lowercased, simple substring match — this is a nudge,
# not an enforcement, so high precision is fine and low recall is acceptable.
_NICHE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "27": ("teach", "tutorial", "explain", "education", "lesson", "learn"),
    "26": ("how to", "diy", "guide", "tips", "tricks"),
    "20": ("gaming", "gameplay", "playthrough", "stream", "speedrun"),
    "23": ("comedy", "funny", "skit", "prank", "humor", "joke"),
    "10": ("music", "song", "cover", "remix", "beat", "lyric"),
    "17": ("workout", "fitness", "match", "training", "sport"),
    "28": ("tech", "review", "unbox", "code", "ai", "science"),
}


def _patterns_text(dna: CreatorDna) -> str:
    """Flatten the patterns_jsonb top/bottom video titles + hooks into one lowercased blob."""
    patterns = dna.patterns_jsonb or {}
    parts: list[str] = []
    for key in ("top_videos", "bottom_videos"):
        for v in patterns.get(key, []) or []:
            for field in ("title", "hook_text"):
                txt = v.get(field) or ""
                if txt:
                    parts.append(txt)
    return " ".join(parts).lower()


def detect(
    identity: CreatorIdentity | None,
    dna: CreatorDna | None,
) -> IdentityDnaConflict | None:
    """Return a single nudge to show on the dashboard, or None if no conflict.

    Current detection: the creator's stated niche keywords appear in NONE of
    their top/bottom video titles + hooks. That's the textbook "stated focus
    is X but their actual content is Y" mismatch. We deliberately only flag
    once — adding more detectors is a future iteration.
    """
    if identity is None or dna is None:
        return None
    if not identity.niches:
        return None

    text = _patterns_text(dna)
    if not text:
        # No inferred patterns yet — can't conflict.
        return None

    matched: list[str] = []
    for niche_id in identity.niches:
        keywords = _NICHE_KEYWORDS.get(niche_id, ())
        if not keywords:
            # We don't have keyword hints for this niche; assume no conflict
            # rather than false-positive on a niche we can't detect.
            matched.append(niche_id)
            continue
        if any(kw in text for kw in keywords):
            matched.append(niche_id)

    if matched:
        return None  # at least one stated niche shows up in inferred patterns

    # None of the stated niches appear in the inferred patterns — surface it.
    stated_labels = [label_for(n) or n for n in identity.niches]
    if len(stated_labels) == 1:
        stated_phrase = stated_labels[0]
    else:
        stated_phrase = ", ".join(stated_labels[:-1]) + f" and {stated_labels[-1]}"
    return IdentityDnaConflict(
        message=(
            f"Your stated focus is {stated_phrase}, but your top-performing clips "
            "don't reflect it yet. Want to update your profile, or keep both?"
        ),
        kind="niche_mismatch",
    )
