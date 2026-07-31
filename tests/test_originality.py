"""Originality guard / sameness advisory tests (Issue 375).

Pure-function tests over ``knowledge/originality.py`` — no DB, no Voyage.
Focused on the load-bearing behaviours: the gate is content (embedding)
similarity, never structural fields alone (so a deliberately consistent
creator is not flagged); a single near-identical pair never triggers an
advisory; and the copy contains no strike/ladder/timeline/"new policy"
language and no virality words (Issue 383 corrections).
"""

import re

from knowledge.originality import (
    ClipFingerprint,
    bucket_source_region,
    build_advisory,
    cosine_similarity,
    style_key,
)

# ── Forbidden-language guard (Issue 383 corrections) ────────────────────────

_FORBIDDEN_MECHANICS = re.compile(
    r"\b(strike|ladder|90.day|permanent(?:ly)?\s+remov|three.strike)\b", re.IGNORECASE
)
_FORBIDDEN_URGENCY = re.compile(r"\bnew policy\b", re.IGNORECASE)
_FORBIDDEN_VIRALITY = re.compile(r"\bviral(?:ity)?\b|guarantee", re.IGNORECASE)


def _identical_vector(seed: float, dim: int = 8) -> list[float]:
    return [seed] * dim


def _fp(
    clip_id: str,
    *,
    embedding: list[float] | None,
    duration_s: float = 45.0,
    setup_lead_s: float | None = 5.0,
    source_region: str | None = "early",
    style: str | None = "white_large|blur|True",
    principle: str | None = "clip-the-setup",
) -> ClipFingerprint:
    return ClipFingerprint(
        clip_id=clip_id,
        duration_s=duration_s,
        setup_lead_s=setup_lead_s,
        source_region=source_region,
        style_key=style,
        principle=principle,
        embedding=embedding,
    )


# ── cosine_similarity ────────────────────────────────────────────────────────


def test_cosine_similarity_identical_vectors_is_one() -> None:
    v = [1.0, 2.0, 3.0]
    assert abs(cosine_similarity(v, v) - 1.0) < 1e-9


def test_cosine_similarity_orthogonal_vectors_is_zero() -> None:
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_cosine_similarity_handles_empty_or_mismatched_vectors_without_raising() -> None:
    assert cosine_similarity([], []) == 0.0
    assert cosine_similarity([1.0], [1.0, 2.0]) == 0.0
    assert cosine_similarity([0.0, 0.0], [0.0, 0.0]) == 0.0


# ── bucket_source_region / style_key ────────────────────────────────────────


def test_bucket_source_region_splits_into_thirds() -> None:
    assert bucket_source_region(0.0, 300.0) == "early"
    assert bucket_source_region(150.0, 300.0) == "mid"
    assert bucket_source_region(280.0, 300.0) == "late"


def test_bucket_source_region_none_without_video_duration() -> None:
    assert bucket_source_region(10.0, None) is None
    assert bucket_source_region(10.0, 0.0) is None


def test_style_key_none_for_missing_preset() -> None:
    assert style_key(None) is None
    assert style_key({}) is None  # empty preset is indistinguishable from "no style set"
    assert style_key({"subtitle": "white_large"}) is not None


# ── build_advisory: the honesty-critical behaviours ─────────────────────────


def test_checked_is_false_with_fewer_than_two_embedded_clips() -> None:
    """Can't compare with 0 or 1 embedded clip — this must read as "couldn't check",
    never as a false "all clear"."""
    fps = [_fp("a", embedding=None), _fp("b", embedding=_identical_vector(1.0))]
    out = build_advisory(fps, similarity_threshold=0.9, min_cluster_size=2)
    assert out.checked is False
    assert out.flagged is False
    assert out.message is None


def test_a_single_near_identical_pair_never_triggers_an_advisory() -> None:
    """One pair could be a coincidental callback — require a meaningful cluster."""
    fps = [
        _fp("a", embedding=_identical_vector(1.0)),
        _fp("b", embedding=_identical_vector(1.0)),
        _fp("c", embedding=[0.0, 1.0, 0.0, -1.0, 0.0, 1.0, 0.0, -1.0]),
    ]
    out = build_advisory(fps, similarity_threshold=0.99, min_cluster_size=3)
    assert out.checked is True
    assert out.flagged is False
    assert out.cluster_size == 2  # the pair was seen, just below the minimum
    assert out.message is None


def test_a_meaningful_cluster_of_near_identical_content_is_flagged() -> None:
    cluster_vec = _identical_vector(1.0)
    fps = [_fp(f"clip-{i}", embedding=cluster_vec) for i in range(4)] + [
        _fp("distinct", embedding=[1.0, -1.0, 1.0, -1.0, 1.0, -1.0, 1.0, -1.0])
    ]
    out = build_advisory(fps, similarity_threshold=0.9, min_cluster_size=4)
    assert out.checked is True
    assert out.flagged is True
    assert out.cluster_size == 4
    assert set(out.similar_clip_ids) == {f"clip-{i}" for i in range(4)}
    assert out.message is not None


def test_consistent_format_alone_does_not_trigger_the_advisory() -> None:
    """The exact case the DNA feature serves: same length/style/region every
    time, but DIFFERENT spoken content. Structural sameness must never be the
    gate on its own."""
    # Standard basis vectors — every pair is exactly orthogonal (cosine == 0),
    # i.e. genuinely different spoken content, regardless of identical structure.
    fps = [
        _fp(
            f"clip-{i}",
            embedding=[1.0 if j == i else 0.0 for j in range(5)],
            duration_s=45.0,
            setup_lead_s=5.0,
            source_region="early",
            style="white_large|blur|True",
        )
        for i in range(5)
    ]
    out = build_advisory(fps, similarity_threshold=0.9, min_cluster_size=4)
    assert out.flagged is False, "identical structure but distinct content must not be flagged"


def test_common_structural_features_reported_only_on_a_flagged_cluster() -> None:
    cluster_vec = _identical_vector(2.0)
    fps = [
        _fp(f"clip-{i}", embedding=cluster_vec, duration_s=45.0, setup_lead_s=5.0) for i in range(4)
    ]
    out = build_advisory(fps, similarity_threshold=0.9, min_cluster_size=4)
    assert out.flagged is True
    assert "length" in out.common_structural_features
    assert "pacing (setup lead)" in out.common_structural_features


def test_clips_without_transcript_coverage_are_excluded_not_treated_as_different() -> None:
    """A clip with embedding=None (no transcript overlap) must not silently
    count as 'proven different' — it is simply excluded from comparison."""
    cluster_vec = _identical_vector(1.0)
    fps = [_fp(f"clip-{i}", embedding=cluster_vec) for i in range(4)] + [
        _fp("no-transcript", embedding=None)
    ]
    out = build_advisory(fps, similarity_threshold=0.9, min_cluster_size=4)
    assert out.checked is True
    assert out.flagged is True
    assert "no-transcript" not in out.similar_clip_ids


# ── Honesty / copy tests (Issue 383 corrections) ────────────────────────────


def test_advisory_copy_has_no_strike_ladder_or_timeline_language() -> None:
    cluster_vec = _identical_vector(1.0)
    fps = [_fp(f"clip-{i}", embedding=cluster_vec) for i in range(4)]
    out = build_advisory(fps, similarity_threshold=0.9, min_cluster_size=4)
    assert out.message is not None
    assert not _FORBIDDEN_MECHANICS.search(out.message), out.message
    assert not _FORBIDDEN_URGENCY.search(out.message), out.message
    assert not _FORBIDDEN_VIRALITY.search(out.message), out.message


def test_advisory_copy_never_certifies_eligibility_or_speaks_for_youtube() -> None:
    cluster_vec = _identical_vector(1.0)
    fps = [_fp(f"clip-{i}", embedding=cluster_vec) for i in range(4)]
    out = build_advisory(fps, similarity_threshold=0.9, min_cluster_size=4)
    assert out.message is not None
    assert "may affect monetization eligibility" in out.message
    assert "not a certification" in out.message
    assert "or a statement from YouTube" in out.message


def test_advisory_copy_cites_a_dated_policy_link() -> None:
    cluster_vec = _identical_vector(1.0)
    fps = [_fp(f"clip-{i}", embedding=cluster_vec) for i in range(4)]
    out = build_advisory(fps, similarity_threshold=0.9, min_cluster_size=4)
    assert out.policy_url == "https://support.google.com/youtube/answer/1311392"
    assert out.policy_checked  # non-empty date string
    assert out.policy_url in (out.message or "")
    assert out.policy_checked in (out.message or "")
