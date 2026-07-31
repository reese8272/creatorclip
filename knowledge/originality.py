"""Originality guard — per-creator sameness advisory (Issue 375).

⛔ READ BEFORE EDITING THE COPY. Issue 383 (2026-07-30) verified three claims
that circulated about YouTube's "inauthentic content" policy and found them
UNSUPPORTED by any primary source:

1. There is **no three-strike ladder**. YouTube's own monetization-policy page
   describes enforcement as ranging from limiting ad earnings to suspending or
   terminating monetization — no numbered strikes, no fixed timeline. Never
   state a consequence mechanism, a strike count, or a timeline here.
2. The policy is **not new**. It was renamed "repetitious content" ->
   "inauthentic content" on 15 July 2025; 16 July 2026 was a *clarification*
   into three categories, not a new policy. Never use "new policy" / urgency
   framing.
3. Clips ARE monetizable with commentary, critical review, or added narrative.
   The disallowed pattern is "clips of moments ... edited together with little
   or no narrative." The bar, in YouTube's own words: "Be your original
   creation. If you borrow content from someone else, you need to change it
   significantly to make it your own."

Source: https://support.google.com/youtube/answer/1311392 (checked 2026-07-30).
See docs/COMPETITIVE_RESEARCH.md § "2026-07-30 refresh — corrections" for the
full verification writeup.

This module is advisory only. It never certifies monetization eligibility and
never claims to speak for YouTube — see ``_build_message`` for the exact copy.

Isolation: every ``ClipFingerprint`` and every comparison in this module is
scoped to ONE creator's own clips. Cross-creator embedding comparison would
defeat the purpose (the point is a creator's own repetition, not a similarity
search across tenants) and is never performed — the router is responsible for
only ever passing this module one creator's clips.

Design note — why the gate is embeddings, not structural fields: duration,
setup lead, source region, and render style are all things a creator sets
DELIBERATELY for a consistent brand (the exact use case the DNA feature
serves). Gating on those would nag creators for being consistent. The gate is
cosine similarity over each clip's OWN spoken-content excerpt (actual
transcript text, not the engine's selection reasoning) — structural agreement
is reported only as corroborating detail on an already-flagged cluster.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

POLICY_URL = "https://support.google.com/youtube/answer/1311392"
POLICY_CHECKED = "2026-07-30"

_DURATION_BUCKET_S = 10.0
_SETUP_LEAD_BUCKET_S = 5.0

_STRUCTURAL_LABELS: dict[str, str] = {
    "duration_bucket": "length",
    "setup_lead_bucket": "pacing (setup lead)",
    "source_region": "position in the source video",
    "style_key": "render style",
}


@dataclass(frozen=True)
class ClipFingerprint:
    """One clip's structural + (optional) semantic signature.

    ``embedding`` is None when the clip has no stored content embedding yet
    (e.g. Voyage not configured, or no transcript coverage over the clip's
    window) — ``build_advisory`` treats those clips as "can't compare", never
    as "different".
    """

    clip_id: str
    duration_s: float | None
    setup_lead_s: float | None
    source_region: str | None
    style_key: str | None
    principle: str | None
    embedding: list[float] | None = None

    @property
    def duration_bucket(self) -> str | None:
        if self.duration_s is None:
            return None
        step = int(_DURATION_BUCKET_S)
        return f"{int(self.duration_s // _DURATION_BUCKET_S) * step}s"

    @property
    def setup_lead_bucket(self) -> str | None:
        if self.setup_lead_s is None:
            return None
        step = int(_SETUP_LEAD_BUCKET_S)
        return f"{int(self.setup_lead_s // _SETUP_LEAD_BUCKET_S) * step}s"


def bucket_source_region(start_s: float, video_duration_s: float | None) -> str | None:
    """Bucket a clip's position within its source video into thirds.

    A per-clip proxy for "source region" — ``CreatorDna.best_source_region``
    is channel-wide, not per-clip, so there is no existing per-clip field to
    reuse here.
    """
    if not video_duration_s or video_duration_s <= 0:
        return None
    frac = max(0.0, min(1.0, start_s / video_duration_s))
    if frac < 1 / 3:
        return "early"
    if frac < 2 / 3:
        return "mid"
    return "late"


def style_key(style_preset: dict | None) -> str | None:
    """Stable string signature of the creator-chosen render style, or None."""
    if not style_preset:
        return None
    return "|".join(
        str(style_preset.get(k)) for k in ("subtitle", "background", "captions_enabled")
    )


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity in [-1, 1]. Returns 0.0 for empty/mismatched/zero vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a < 1e-12 or norm_b < 1e-12:
        return 0.0
    return dot / (norm_a * norm_b)


def _connected_components(clip_ids: list[str], edges: set[tuple[str, str]]) -> list[list[str]]:
    """Group clip ids into clusters joined by near-identical (undirected) edges."""
    adjacency: dict[str, set[str]] = {cid: set() for cid in clip_ids}
    for a, b in edges:
        adjacency[a].add(b)
        adjacency[b].add(a)

    seen: set[str] = set()
    components: list[list[str]] = []
    for cid in clip_ids:
        if cid in seen:
            continue
        stack = [cid]
        component: list[str] = []
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            component.append(node)
            stack.extend(adjacency[node] - seen)
        components.append(component)
    return components


def _common_structural_features(cluster: list[ClipFingerprint]) -> list[str]:
    """Structural fields every clip in the cluster shares exactly — display-only.

    Never the trigger for flagging (see module docstring) — reported so the
    advisory can say what the cluster ALSO has in common, once flagged on
    content grounds.
    """
    if len(cluster) < 2:
        return []
    checks: tuple[tuple[str, list[str | None]], ...] = (
        ("duration_bucket", [c.duration_bucket for c in cluster]),
        ("setup_lead_bucket", [c.setup_lead_bucket for c in cluster]),
        ("source_region", [c.source_region for c in cluster]),
        ("style_key", [c.style_key for c in cluster]),
    )
    common: list[str] = []
    for name, values in checks:
        if values[0] is not None and all(v == values[0] for v in values):
            common.append(_STRUCTURAL_LABELS[name])
    return common


def _build_message(cluster_size: int, window: int, common_features: list[str]) -> str:
    """Advisory copy. Every clause here is load-bearing — see module docstring.

    - States a fact (count) before any inference.
    - "may affect monetization eligibility", never a certainty or a mechanism.
    - Dated, linked citation so the claim is auditable when YouTube revises it.
    - Explicit disclaimer: advisory, not a certification, not YouTube speaking.
    """
    feature_phrase = ""
    if common_features:
        feature_phrase = f" They also share the same {', '.join(common_features)}."
    return (
        f"{cluster_size} of your last {window} clips are near-identical in spoken content."
        f"{feature_phrase} YouTube's guidance on original content says mass-produced or "
        f"repetitive output may affect monetization eligibility — see {POLICY_URL} "
        f"(checked {POLICY_CHECKED}). This is an advisory based on your own recent clips, "
        "not a certification of monetization eligibility or a statement from YouTube."
    )


@dataclass(frozen=True)
class SamenessAdvisory:
    """Result of one creator's sameness check. See ``build_advisory``."""

    checked: bool
    flagged: bool
    window: int
    cluster_size: int
    similar_clip_ids: list[str]
    common_structural_features: list[str]
    message: str | None
    policy_url: str
    policy_checked: str


def build_advisory(
    fingerprints: list[ClipFingerprint],
    *,
    similarity_threshold: float,
    min_cluster_size: int,
) -> SamenessAdvisory:
    """Pure aggregation: cluster a creator's recent clips by content sameness.

    Gates ONLY on embedding cosine similarity — never on structural fields
    alone (see module docstring for why). ``checked=False`` means the
    comparison could not run at all (fewer than 2 clips have a stored
    embedding) — a distinct, honest state from "checked and found nothing",
    which the API/UI must not collapse into a false "all clear".
    """
    window = len(fingerprints)
    embedded = [fp for fp in fingerprints if fp.embedding]

    if len(embedded) < 2:
        return SamenessAdvisory(
            checked=False,
            flagged=False,
            window=window,
            cluster_size=0,
            similar_clip_ids=[],
            common_structural_features=[],
            message=None,
            policy_url=POLICY_URL,
            policy_checked=POLICY_CHECKED,
        )

    edges: set[tuple[str, str]] = set()
    for i in range(len(embedded)):
        for j in range(i + 1, len(embedded)):
            a, b = embedded[i], embedded[j]
            assert a.embedding is not None and b.embedding is not None  # narrowed by filter above
            if cosine_similarity(a.embedding, b.embedding) >= similarity_threshold:
                edges.add((a.clip_id, b.clip_id))

    components = _connected_components([fp.clip_id for fp in embedded], edges)
    largest = max(components, key=len) if components else []

    if len(largest) < min_cluster_size:
        return SamenessAdvisory(
            checked=True,
            flagged=False,
            window=window,
            cluster_size=len(largest),
            similar_clip_ids=[],
            common_structural_features=[],
            message=None,
            policy_url=POLICY_URL,
            policy_checked=POLICY_CHECKED,
        )

    by_id = {fp.clip_id: fp for fp in embedded}
    cluster = [by_id[cid] for cid in largest]
    common = _common_structural_features(cluster)
    message = _build_message(len(cluster), window, common)

    return SamenessAdvisory(
        checked=True,
        flagged=True,
        window=window,
        cluster_size=len(cluster),
        similar_clip_ids=largest,
        common_structural_features=common,
        message=message,
        policy_url=POLICY_URL,
        policy_checked=POLICY_CHECKED,
    )
