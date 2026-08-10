"""
Rank scored clip candidates and persist them to the clips table.
"""

import asyncio
import logging
import math
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from clip_engine.candidates import extract_candidates
from clip_engine.scoring import score_candidates
from clip_engine.sentence_snap import snap_candidates_to_sentences
from models import Clip, ClipFormat, RenderStatus

logger = logging.getLogger(__name__)


def _safe_score(value: object) -> float:
    """Coerce a clip score to a sortable float, mapping NaN/None/garbage to -inf.

    Python's sort places NaN unpredictably (comparisons return False), so a single
    NaN score makes the whole ranking order undefined. Sorting NaN/missing scores to
    the bottom deterministically keeps a broken score from leaking to rank 1. (Issue 328)
    """
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float("-inf")
    return f if math.isfinite(f) else float("-inf")


def rank_candidates(candidates: list[dict]) -> list[dict]:
    """Sort by score descending, assign rank (1 = best). Pure function."""
    ranked = sorted(candidates, key=lambda c: _safe_score(c.get("score", 0.0)), reverse=True)
    for i, c in enumerate(ranked):
        c["rank"] = i + 1
    return ranked


# Overlap coefficient (intersection-over-minimum) threshold for the post-ranking
# containment pass (Issue 429). IoU under-reports engulfed windows — the live
# contained pair scored IoU 0.39 (under the 0.5 NMS threshold) but IoMin 1.0.
# 0.8 fires only on the asymmetric-containment blind spot: two equal-length
# windows admitted by IoU-0.5 NMS reach at most IoMin ≈ 0.67, so the pass never
# re-litigates what the signal-priority union deliberately kept.
_CONTAINMENT_THRESHOLD = 0.8

# Absolute seconds of shared speech two kept clips may carry (Issue 441).
#
# This is a SECOND, independent predicate — lowering _CONTAINMENT_THRESHOLD is
# not an option. It is pinned by tests/test_merge.py and deliberately set above
# the ≈0.67 IoMin ceiling that two equal-length IoU-0.5 NMS survivors reach, so
# any ratio low enough to catch the live failures (IoMin 0.419 and 0.27) would
# also start dropping pairs the signal-priority union intentionally kept.
#
# Seconds express the actual editorial rule — don't ship the same sentence
# twice — and are immune to the length asymmetry that defeats every ratio. 3 s
# is about one spoken sentence. On the live set it catches the 17.4 s, 9.1 s,
# 4.1 s and 3.3 s pairs (including rank 2's closing line being rank 6's opening
# line verbatim) and leaves the 0.8 s snap artifact alone.
_MAX_OVERLAP_S = 3.0


def window_containment(a: dict, b: dict) -> float:
    """Overlap coefficient of two candidates' [setup_start_s, end_s] windows:
    intersection / min(length). 1.0 whenever one window contains the other,
    regardless of the size difference; 0.0 on degenerate windows."""
    a_start, a_end = a["setup_start_s"], a["end_s"]
    b_start, b_end = b["setup_start_s"], b["end_s"]
    shorter = min(a_end - a_start, b_end - b_start)
    if shorter <= 0.0:
        return 0.0
    inter = max(0.0, min(a_end, b_end) - max(a_start, b_start))
    return inter / shorter


def window_overlap_s(a: dict, b: dict) -> float:
    """Seconds of shared speech between two candidates' windows (Issue 441)."""
    inter = min(a["end_s"], b["end_s"]) - max(a["setup_start_s"], b["setup_start_s"])
    return max(0.0, inter)


def _is_duplicate_of(cand: dict, kept: dict, threshold: float) -> bool:
    """Whether ``cand`` duplicates ``kept`` — by containment ratio OR by absolute
    shared seconds. The two predicates catch different failures: containment
    catches an engulfed window of any length, seconds catch a partial overlap
    that no ratio can flag without re-litigating the NMS union."""
    return (
        window_containment(cand, kept) >= threshold
        or window_overlap_s(cand, kept) >= _MAX_OVERLAP_S
    )


def suppress_contained(ranked: list[dict], threshold: float = _CONTAINMENT_THRESHOLD) -> list[dict]:
    """Drop lower-ranked candidates whose window is (near-)contained in a kept
    one (Issue 429 — the creator saw 8 "clips" but 7 stories).

    Runs POST-ranking and PRE-trim: the union in merge.py and
    ``extract_candidates`` are untouched, dropped slots refill from the pool on
    merit, and — because it runs before persistence — the preference reranker
    can never resurrect a dropped duplicate. Rank 1 is always kept, so a
    non-empty input can never come back empty. Survivor ranks are renumbered
    1..n (``persist_ranked_clips`` requires a dense rank sequence).
    """
    kept: list[dict] = []
    for cand in ranked:
        dup_of = next((k for k in kept if _is_duplicate_of(cand, k, threshold)), None)
        if dup_of is not None:
            logger.info(
                "containment pass dropped [%.1f, %.1f] (origin=%s) — %.0f%% inside / %.1fs "
                "shared with kept [%.1f, %.1f] (origin=%s)",
                cand["setup_start_s"],
                cand["end_s"],
                cand.get("origin", "signal"),
                window_containment(cand, dup_of) * 100,
                window_overlap_s(cand, dup_of),
                dup_of["setup_start_s"],
                dup_of["end_s"],
                dup_of.get("origin", "signal"),
            )
            continue
        kept.append(cand)
    for i, c in enumerate(kept):
        c["rank"] = i + 1
    return kept


def is_shortlisted(rank: int | None, size: int | None = None) -> bool:
    """Selection-only cut (Issue 377): True when ``rank`` falls within the top
    ``size`` (default ``settings.SHORTLIST_SIZE``).

    This is a PRESENTATION cut, not a scoring or ranking change — it never
    touches ``score``/``rank`` and never drops a candidate from the persisted
    set. It runs strictly AFTER ``rank_candidates`` has already produced a
    total order, so ties are resolved exactly as they always were (the stable
    sort in ``rank_candidates`` keeps the original candidate-extraction order
    for equal scores) — the cut just reads the resulting rank.

    ``rank is None`` (a creator-made selection, Issue 373 — never engine-scored)
    is never shortlisted: there is no principle/reasoning/score to argue a case
    with, so a "top pick" framing would be dishonest for it. It still shows up
    under "show all candidates".
    """
    if rank is None:
        return False
    from config import settings

    n = size if size is not None else settings.SHORTLIST_SIZE
    return rank <= n


async def rerank_with_preference(
    clips: list[Clip],
    session: AsyncSession,
    creator_id: uuid.UUID,
) -> list[Clip]:
    """
    Re-rank an already-scored clip list using the creator's preference model.
    Falls back silently if no model is trained yet (below threshold).
    """
    from preference.features import clip_features
    from preference.model import preference_weight
    from preference.train import load_latest

    scorer = await load_latest(session, creator_id)
    if scorer is None:
        return clips

    # Honest personalization threshold: below it the model gets weight 0, so the
    # DNA + signal ranking is returned unchanged (no false personalization). The
    # weight ramps with the creator's own feedback volume. (Issue 60)
    weight = preference_weight(scorer.label_count)
    if weight == 0.0:
        return clips

    def _features(clip: Clip) -> list[float]:
        feats_dict = (clip.signals_jsonb or {}).get("features", {})
        return clip_features(
            signal_density=feats_dict.get("signal_density", 0.0),
            hook_energy=feats_dict.get("hook_energy", 0.0),
            silence_ratio=feats_dict.get("silence_ratio", 0.0),
            dna_match=clip.dna_match,
            clip_duration_s=feats_dict.get("clip_duration_s", 0.0),
            setup_length_s=feats_dict.get("setup_length_s", 0.0),
            has_retention_spike=feats_dict.get("has_retention_spike", False),
            has_laughter=feats_dict.get("has_laughter", False),
        )

    # Score everything BEFORE mutating, so a broken model leaves the DNA ranking
    # untouched (honest fallback) instead of half-blended. (Issue 71)
    try:
        pref_scores = [scorer.predict_score(_features(clip)) for clip in clips]
    except Exception as exc:
        logger.warning("Preference rerank failed (%s) — keeping DNA ranking", exc)
        return clips

    # A non-finite prediction (NaN/inf) would blend into clip.score and make the
    # subsequent sort order undefined. Treat it as a broken model and fall back to
    # the DNA ranking untouched — the same honest behavior as a predict exception.
    if not all(math.isfinite(s) for s in pref_scores):
        logger.warning(
            "Preference model produced a non-finite score for creator %s — keeping DNA ranking",
            creator_id,
        )
        return clips

    for clip, pref_score in zip(clips, pref_scores, strict=True):
        clip.score = (1.0 - weight) * (clip.score or 0.0) + weight * pref_score

    clips.sort(key=lambda c: _safe_score(c.score), reverse=True)
    for i, clip in enumerate(clips):
        clip.rank = i + 1

    return clips


async def load_existing_clips(
    session: AsyncSession,
    video_id: uuid.UUID,
    creator_id: uuid.UUID,
) -> list[Clip]:
    """Return this creator's already-persisted clips for ``video_id`` in rank order.

    The Issue-61 idempotency guard: callers check this BEFORE spending LLM tokens
    on scoring, and ``persist_ranked_clips`` re-checks it before inserting so a
    Celery redelivery / concurrent request can never delete+reinsert (which would
    cascade-delete Clip.feedback / Clip.outcome). Per-creator isolation: the
    creator_id predicate backs the RLS policy rather than replacing it.
    """
    result = await session.execute(
        select(Clip)
        .where(Clip.video_id == video_id, Clip.creator_id == creator_id)
        .order_by(Clip.rank)
    )
    return list(result.scalars().all())


async def score_and_rank(
    video_id: uuid.UUID,
    creator_id: uuid.UUID,
    timeline: dict,
    dna_brief: str | None = None,
    transcript_segments: list | None = None,
    max_candidates: int = 8,
    ledger_session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]] | None = None,
    style_notes: str | None = None,
    video_context: dict | None = None,
    exclude_windows: list[dict] | None = None,
) -> list[dict]:
    """Extract candidates → merge LLM moments → score (ONE LLM call) → rank → trim.

    ``exclude_windows`` (Issue 431 — append-mode regeneration): windows of
    ALREADY-PERSISTED clips (``{"setup_start_s", "end_s"}`` dicts). After the
    internal dedup, any candidate whose overlap coefficient vs an excluded
    window reaches the containment threshold is dropped — regeneration never
    re-proposes a story the creator already has (the stored VideoContext
    moments repeat verbatim on re-runs, so this filter is what makes the
    output NEW). Survivors are renumbered densely before the trim.

    Session-free. Split out of the old ``generate_and_rank_clips`` (Issue 82b): the
    LLM round-trip here can take 30–120 s, so no DB session may be held across it —
    callers release their session first and persist the returned ranking via
    ``persist_ranked_clips`` on a freshly acquired session. ``ledger_session_factory``
    lets the scoring layer open a short-lived session AFTER the LLM call for the
    usage-ledger write.

    ``video_context`` (Issue 416) is the stored ``VideoContext.context_jsonb`` (or
    None when the context pass was skipped/disabled). Its validated ``moments``
    become llm-origin candidates unioned with the signal candidates under
    signal-priority NMS (clip_engine/merge.py), all scored in the ONE existing
    Sonnet call, then trimmed post-ranking to ``max_candidates`` — weak candidates
    are displaced on merit, and row/render/shortlist behavior downstream is
    unchanged. Absent context ⇒ byte-identical behavior to the pre-416 pipeline.
    """
    # Candidate extraction is CPU-bound (numpy array build + scipy find_peaks over
    # duration/0.5 samples). Offload it so it can't stall the API event loop and the
    # other concurrent requests on this worker. (Issue C)
    #
    # Sentence snapping moved to the segment-aware post-extraction pass (Issue 428):
    # extract_candidates no longer receives words — sentence_snap is the single
    # snapping authority, working on utterance segments instead of a flat word list.
    from config import settings

    # Signal pool cap decoupled from the persisted count (2026-08-05): the pool
    # (CLIP_SIGNAL_POOL_MAX signal + LLM_CANDIDATES_MAX llm) feeds the scored
    # merge; the [:max_candidates] trim below decides what persists.
    segments = list(transcript_segments or [])
    duration_s = float(timeline.get("duration_s", 0.0))
    signal_pool_max = max(settings.CLIP_SIGNAL_POOL_MAX, max_candidates)
    candidates = await asyncio.to_thread(
        lambda: snap_candidates_to_sentences(
            extract_candidates(timeline, signal_pool_max), segments, duration_s
        )
    )

    moments = (video_context or {}).get("moments") or []
    if moments:
        from clip_engine.merge import llm_moments_to_candidates, merge_candidates

        llm_candidates = await asyncio.to_thread(
            lambda: llm_moments_to_candidates(moments, timeline, segments=segments or None)
        )
        if llm_candidates:
            candidates = merge_candidates(candidates, llm_candidates)

    if not candidates:
        logger.info("No candidates found for video %s", video_id)
        return []

    scored = await score_candidates(
        candidates,
        timeline,
        dna_brief,
        transcript_segments,
        creator_id=creator_id,
        ledger_session_factory=ledger_session_factory,
        style_notes=style_notes,
    )
    # Containment pass (Issue 429) then post-ranking trim (Issue 416): the
    # merged pool can exceed max_candidates; near-duplicate contained windows
    # are dropped first (freed slots refill from the pool below the cut), then
    # displacement happens HERE on scored merit, never pre-scoring.
    ranked = suppress_contained(rank_candidates(scored))
    if exclude_windows:
        ranked = [
            c
            for c in ranked
            # Same two-predicate test as the containment pass (Issue 441) — a
            # regenerated clip that shares 3 s of speech with an existing one is
            # as duplicate as a contained one.
            if not any(_is_duplicate_of(c, w, _CONTAINMENT_THRESHOLD) for w in exclude_windows)
        ]
        for i, c in enumerate(ranked):
            c["rank"] = i + 1
    return ranked[:max_candidates]


async def persist_ranked_clips(
    session: AsyncSession,
    video_id: uuid.UUID,
    creator_id: uuid.UUID,
    ranked: list[dict],
) -> list[Clip]:
    """Persist a pre-computed ranking to the clips table. DB-only — no LLM calls.

    Idempotent (Issue 61): if clips already exist for this video the call is a
    no-op and the existing clips are returned in rank order. The pipeline
    generates clips exactly once; a Celery redelivery (at-least-once) must NOT
    delete+reinsert, because Clip.feedback / Clip.outcome cascade-delete and that
    would destroy the creator's feedback labels and published-clip outcomes.
    Returns persisted Clip ORM objects in rank order.

    Concurrent-safe (Issue 361): the check above is check-then-insert, so two
    concurrent executions (router racing the worker pipeline, or two at-least-once
    Celery deliveries) can BOTH pass the guard. The ``uq_clips_video_rank``
    constraint (migration 0046, DEFERRABLE so its check runs at COMMIT) makes the
    loser's commit raise ``IntegrityError`` — it rolls back and returns the
    winner's set instead of double-inserting.
    """
    existing = await load_existing_clips(session, video_id, creator_id)
    if existing:
        logger.info(
            "Clips already exist for video %s (%d) — skipping regeneration", video_id, len(existing)
        )
        return existing

    if not ranked:
        return []

    # The existing-clips early-return above short-circuits when ANY clip exists
    # for this video (Issue 61 idempotency). Local Issue 46's selective-DELETE
    # branch was unreachable under that guard and is dropped during the merge —
    # the stronger guarantee here is "never delete+reinsert", which makes the
    # 46-style protection for done/running clips automatic.

    # dna_match stays the raw DNA-only fit from Claude, NOT the composite score —
    # seeding it with the composite would make it collinear with its own
    # label-generating signal in the preference feature vector. (Issue 103 #5)
    # signals_jsonb.origin (Issue 416): "llm" for context-pass candidates,
    # "signal" for audio-energy peaks.
    clips: list[Clip] = [_new_clip_row(video_id, creator_id, c, c["rank"]) for c in ranked]
    session.add_all(clips)

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        # Only a uq_clips_video_rank violation means we lost the
        # concurrent-generation race (the constraint is deferred, so it
        # surfaces here). Any other integrity error (FK, NOT NULL) is a real
        # defect — re-raise instead of mislabeling it as a lost race.
        if "uq_clips_video_rank" not in str(exc.orig):
            raise
        logger.info(
            "Concurrent clip persist detected for video %s — returning existing set", video_id
        )
        return await load_existing_clips(session, video_id, creator_id)
    for clip in clips:
        await session.refresh(clip)

    # Apply the creator's preference model on top of the DNA/signal ranking.
    # No-ops below the personalization threshold or when no model is trained. (Issue 60)
    clips = await rerank_with_preference(clips, session, creator_id)
    await session.commit()

    logger.info("Generated %d ranked clips for video %s", len(clips), video_id)
    return clips


def _new_clip_row(video_id: uuid.UUID, creator_id: uuid.UUID, c: dict, rank: int) -> Clip:
    """One engine-generated Clip row from a scored candidate dict (shared by
    the initial persist and the Issue-431 append path)."""
    return Clip(
        video_id=video_id,
        creator_id=creator_id,
        setup_start_s=c["setup_start_s"],
        start_s=c["start_s"],
        end_s=c["end_s"],
        peak_s=c["peak_s"],
        score=c.get("score"),
        dna_match=c.get("dna_match"),
        signals_jsonb={
            "features": c.get("features", {}),
            "principle": c.get("principle", ""),
            "reasoning": c.get("reasoning", ""),
            "origin": c.get("origin", "signal"),
        },
        format=ClipFormat.short,
        render_status=RenderStatus.pending,
        rank=rank,
    )


async def append_ranked_clips(
    session: AsyncSession,
    video_id: uuid.UUID,
    creator_id: uuid.UUID,
    ranked: list[dict],
) -> list[Clip]:
    """APPEND newly-scored clips after the video's existing set (Issue 431).

    The counterpart of ``persist_ranked_clips`` for "Generate more clips":
    - NO existing-clips no-op (existing clips are the precondition, not a race
      loser) — the caller enforces the regeneration caps.
    - Ranks continue from the video's max engine rank (creator-made clips
      carry ``rank=None`` and are ignored), so ``uq_clips_video_rank`` holds.
    - ``rerank_with_preference`` is deliberately SKIPPED: it renumbers ranks
      from 1 across whatever list it is given, which would collide with the
      existing set — appended clips keep their scored order (DECISIONS
      2026-08-05). On a ``uq_clips_video_rank`` race (concurrent append), the
      offset is re-derived once and the insert retried; a second collision
      re-raises.
    Returns only the NEWLY appended Clip rows, in rank order.
    """
    if not ranked:
        return []

    from sqlalchemy import func

    async def _max_engine_rank() -> int:
        result = await session.execute(
            select(func.max(Clip.rank)).where(
                Clip.video_id == video_id, Clip.creator_id == creator_id
            )
        )
        return result.scalar() or 0

    for attempt in range(2):
        base = await _max_engine_rank()
        clips = [_new_clip_row(video_id, creator_id, c, base + i + 1) for i, c in enumerate(ranked)]
        session.add_all(clips)
        try:
            await session.commit()
            break
        except IntegrityError as exc:
            await session.rollback()
            if "uq_clips_video_rank" not in str(exc.orig) or attempt == 1:
                raise
            logger.info(
                "Concurrent append detected for video %s — re-deriving the rank offset", video_id
            )
    for clip in clips:
        await session.refresh(clip)
    logger.info("Appended %d clips for video %s (ranks from %d)", len(clips), video_id, base + 1)
    return clips
