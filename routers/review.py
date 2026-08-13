"""
Feedback capture: upvote / downvote / skip / trim / format.
Each action persists to clip_feedback and is used by the preference model.

Also owns POST /clips/{clip_id}/trim-render (Wave-1 ready pass): turns the
Review screen's trim window into a real re-render via the existing
``edit_clip`` machinery — the result lands in ``cleaned_render_uri`` and is
swapped in by the existing ``POST /clips/{clip_id}/clean/confirm``.
"""

import asyncio
import logging
import math
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_creator
from billing.ledger import check_positive_balance
from billing.spend_guard import require_budget
from clip_engine.edits import MIN_KEEP_SEGMENT_S
from db import get_session
from flags import require_flag
from limiter import RENDER_DAILY_LIMIT, creator_key, limiter
from models import (
    TRIAGE_BY_FEEDBACK_ACTION,
    Clip,
    ClipFeedback,
    ClipTriage,
    Creator,
    FeedbackAction,
)
from routers._enqueue import enqueue_stream_task
from routers._owned import get_owned
from routers._schemas import TaskQueuedOut

router = APIRouter(prefix="/clips", tags=["review"])
logger = logging.getLogger(__name__)

# Issue 235 — the three actions that constitute a "keep" (the activation event).
# upvote = explicit approval; trim = editorial keep; format = deliberate render.
# downvote and skip are rejections; they are not activation signals.
_KEEP_ACTIONS: frozenset[FeedbackAction] = frozenset(
    {FeedbackAction.upvote, FeedbackAction.trim, FeedbackAction.format}
)


class FeedbackOut(BaseModel):
    # `id` is None for `skip` (Issue 472): skip is acknowledged, never persisted,
    # so there is no feedback row to identify.
    id: str | None
    action: str


# Issue 444 — the feedback action a triage move implies. Moving a clip between
# piles IS a change of verdict, so it records one: the pile and the model can
# then never hold different opinions. Safe only because preference/train.py now
# keeps one label per clip (the newest) — before that dedup this would have
# stacked contradictory samples, which is the bug the issue exists to fix.
#
# `pending` maps to `skip`, which is a genuine RETRACTION: it supersedes the
# earlier verdict in the training partition and then drops out, because `skip`
# is not a trainable action. Sending a clip back to the review queue therefore
# withdraws its label rather than leaving a stale one behind.
_TRIAGE_TO_ACTION: dict[ClipTriage, FeedbackAction] = {
    ClipTriage.kept: FeedbackAction.upvote,
    ClipTriage.dropped: FeedbackAction.downvote,
    ClipTriage.pending: FeedbackAction.skip,
}


class TriageIn(BaseModel):
    # extra="forbid" is load-bearing: without it a client sending {"state": …}
    # instead of {"triage": …} gets a cheerful 200 and a silent no-op.
    model_config = ConfigDict(extra="forbid")

    triage: ClipTriage


class TriageOut(BaseModel):
    id: str
    triage: str


_FEEDBACK_NOTE_MAX_LEN = 2000


def _validate_trim_pair(s: float | None, e: float | None) -> None:
    """Issue-339 numeric checks shared by FeedbackRequest and TrimRenderIn.

    Rejects non-finite, negative, or inverted trim windows. Raises
    ``ValueError`` so pydantic model validators surface it as a 422.
    """
    for val, label in ((s, "trim_start_s"), (e, "trim_end_s")):
        if val is not None and not math.isfinite(val):
            raise ValueError(f"{label} must be a finite number")
        if val is not None and val < 0:
            raise ValueError(f"{label} must be >= 0")
    if s is not None and e is not None and s >= e:
        raise ValueError("trim_start_s must be less than trim_end_s")


def _clip_duration_s(clip: Clip) -> float:
    """Duration of the DELIVERED render in CLIP-RELATIVE seconds.

    The timebase shared by /transcript, /cuts, /feedback and /trim-render.
    Both trim routes derive their bounds from this one helper so they cannot
    drift. Issue 470: delegates to the shared geometry-aware helper — after a
    confirmed trim/clean the delivered video is shorter than the source window
    (origin ``setup_start_s`` ?? ``start_s``), and trim bounds validated
    against the stale window would pass validation here and then overrun the
    real file in the worker.
    """
    from clip_engine.edits import playable_duration_s

    return playable_duration_s(
        setup_start_s=clip.setup_start_s,
        start_s=clip.start_s,
        end_s=clip.end_s,
        effective_geometry=clip.effective_geometry_jsonb,
    )


class FeedbackRequest(BaseModel):
    action: FeedbackAction
    trim_start_s: float | None = None
    trim_end_s: float | None = None
    chosen_format: str | None = None
    # Issue 118: structured multi-select feedback tags.
    # Approve tags: "titles_fit_style", "editing_matches_pace", "good_hook", "right_length"
    # Deny tags:   "editing_mismatch", "off_brand_topic", "bad_hook", "wrong_length"
    feedback_tags: list[str] | None = None
    # Free-text "Other" note captured alongside tags.
    # Issue 339: max 2000 chars so no unbounded text is persisted without a cap.
    feedback_note: str | None = Field(default=None, max_length=_FEEDBACK_NOTE_MAX_LEN)

    @field_validator("action", mode="before")
    @classmethod
    def coerce_action(cls, v: str) -> FeedbackAction:
        return FeedbackAction(v)

    @model_validator(mode="after")
    def validate_trim(self) -> "FeedbackRequest":
        """Issue 339: reject non-finite, negative, or inverted trim windows.

        Clip-bounds validation (trim within [clip.start_s, clip.end_s]) is
        performed in the route handler after the clip is fetched from the DB.
        """
        if self.trim_start_s is None and self.trim_end_s is None:
            return self  # no trim supplied — nothing to validate
        _validate_trim_pair(self.trim_start_s, self.trim_end_s)
        return self


class TrimRenderIn(BaseModel):
    """Request body for POST /clips/{clip_id}/trim-render.

    ``trim_start_s`` / ``trim_end_s`` are CLIP-RELATIVE seconds over the
    rendered mp4 — origin is ``setup_start_s`` when set, else ``start_s`` —
    the same timebase as ``GET /clips/{id}/transcript`` and
    ``POST /clips/{id}/cuts``.
    """

    model_config = ConfigDict(extra="forbid")

    trim_start_s: float
    trim_end_s: float

    @model_validator(mode="after")
    def validate_trim(self) -> "TrimRenderIn":
        _validate_trim_pair(self.trim_start_s, self.trim_end_s)
        return self


async def _is_first_keep(session: AsyncSession, creator_id: uuid.UUID) -> bool:
    """Return True if this creator has never previously submitted a keep action.

    A "keep" is any of {upvote, trim, format} — the three actions that signal
    "this clip is good enough to use."  The check is idempotent: if a prior
    keep already exists in clip_feedback, we do not fire clip_kept again.

    Issue 235 — activation event idempotency guard.
    """
    result = await session.execute(
        select(
            exists().where(
                ClipFeedback.creator_id == creator_id,
                ClipFeedback.action.in_(list(_KEEP_ACTIONS)),
            )
        )
    )
    return not result.scalar()


@router.post("/{clip_id}/feedback", status_code=201, response_model=FeedbackOut)
@limiter.limit("120/minute", key_func=creator_key)
async def submit_feedback(
    request: Request,
    clip_id: uuid.UUID,
    body: FeedbackRequest,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Record a feedback action for a clip.

    ``skip`` is the one action that is acknowledged but NOT recorded (Issue 472,
    SEV1): on this surface it means "advance past this clip" — UI navigation —
    while in the training partition a ``skip`` row is a RETRACTION that
    supersedes the clip's latest real label and then drops out at the trainable
    filter. The shipped Trim → Skip flow therefore silently erased keep labels
    while the pile stayed ``kept``. Retraction remains available where it is
    meant: ``PUT /clips/{id}/triage`` back to ``pending``.
    """
    clip = await get_owned(session, Clip, clip_id, creator.id, detail="Clip not found")

    # Issue 472 — pure ack: no ClipFeedback row, no triage change, no retrain
    # enqueue, no activation probe. 201 with id=None keeps the response shape
    # stable for clients that fire-and-forget the POST.
    if body.action is FeedbackAction.skip:
        return {"id": None, "action": FeedbackAction.skip.value}

    # Issue 339 (timebase corrected, ready-pass W1): trim values are
    # CLIP-RELATIVE seconds over the rendered mp4 — origin is
    # ``setup_start_s`` when set, else ``start_s``, the same timebase as
    # /transcript, /cuts and /trim-render. The original check compared them
    # against video-absolute start_s/end_s, 422-ing almost any clip that
    # starts later in the source video (negatives/inversion are rejected by
    # the FeedbackRequest model validator).
    if body.trim_start_s is not None or body.trim_end_s is not None:
        s, e = body.trim_start_s, body.trim_end_s
        # Right-edge tolerance of one frame (MIN_KEEP_SEGMENT_S) matches
        # /trim-render and validate_user_cuts: drag-to-end UI rounding puts
        # the trim a few ms past the computed duration — Save must accept
        # exactly what the re-render endpoint accepts.
        max_trim_s = _clip_duration_s(clip) + MIN_KEEP_SEGMENT_S
        if s is not None and s > max_trim_s:
            raise HTTPException(status_code=422, detail="trim_start_s is past the clip end")
        if e is not None and e > max_trim_s:
            raise HTTPException(status_code=422, detail="trim_end_s is past the clip end")

    # Issue 235 — check idempotency BEFORE the commit so the new row is not
    # included in the existence query (the session hasn't flushed yet).
    is_activation = body.action in _KEEP_ACTIONS and await _is_first_keep(session, creator.id)

    feedback = ClipFeedback(
        clip_id=clip_id,
        creator_id=creator.id,
        action=body.action,
        trim_start_s=body.trim_start_s,
        trim_end_s=body.trim_end_s,
        chosen_format=body.chosen_format,
        feedback_tags=body.feedback_tags or None,
        feedback_note=body.feedback_note or None,
    )
    session.add(feedback)
    # Issue 444 — a rating IS a verdict, so the pile moves with it in the same
    # transaction. Deriving triage server-side rather than making the client
    # send both is what makes it structurally impossible for the rating and the
    # pile to disagree. The invariant (pinned by the integration lane): a
    # `kept`/`dropped` pile implies exactly one surviving matching-polarity
    # label in the verdict partition; `pending` implies none. `skip` never
    # reaches this point — it early-returns above without writing a row
    # (Issue 472), so it can neither move the pile nor retract a label; the
    # only retraction path is PUT /clips/{id}/triage back to `pending`, which
    # records the `skip` row that wins the partition and then drops out. The
    # reverse direction is NOT symmetric — PUT /clips/{id}/triage moves a clip
    # between piles while writing the matching label in the same transaction.
    implied_triage = TRIAGE_BY_FEEDBACK_ACTION.get(body.action)
    if implied_triage is not None:
        clip.triage = implied_triage
    await session.commit()
    await session.refresh(feedback)

    from observability import log_event

    log_event(
        "clip_feedback_submitted",
        creator_id=str(creator.id),
        clip_id=str(clip_id),
        action=body.action.value,
    )

    # Issue 235 — emit clip_kept (ACTIVATION EVENT) on first keep per creator.
    # Best-effort: record_event never raises; a telemetry failure must not block
    # the response.  Scheduled via record_event_nowait (Issue 352) so the task
    # is strongly referenced until done — never GC'd mid-execution.
    if is_activation:
        from event_log import record_event_nowait

        record_event_nowait(
            source="backend",
            event="clip_kept",
            creator_id=creator.id,
            extra={"action": body.action.value},
        )
        logger.info(
            "clip_kept activation event: creator=%s clip=%s action=%s",
            creator.id,
            clip_id,
            body.action.value,
        )

    # Retrain the creator's preference model so ranking adapts to this feedback.
    # Shared debounced enqueue (worker.tasks.enqueue_retrain — Issue 473): the
    # task self-debounces and the countdown coalesces bursts, so enqueuing on
    # every feedback write is cheap. (Issue 60)
    from worker.tasks import enqueue_retrain

    await enqueue_retrain(creator.id)

    # Issue 371: tags/notes carry the "why" — feed the style distiller. Only
    # enqueued when substance exists; the task itself debounces + LLM-gates.
    if body.feedback_tags or body.feedback_note:
        from worker.tasks import distill_style_prefs

        await asyncio.to_thread(distill_style_prefs.delay, str(creator.id))

    return {"id": str(feedback.id), "action": feedback.action.value}


@router.put("/{clip_id}/triage", response_model=TriageOut)
@limiter.limit("120/minute", key_func=creator_key)
async def set_clip_triage(
    request: Request,
    clip_id: uuid.UUID,
    body: TriageIn,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Set the creator's current verdict on a clip (Issue 444).

    PUT, not POST, because this is a state transition on a known URI: applying
    the same body twice must leave the same state and must be safe to retry.
    POST would say "append to a collection", which is what /feedback already is.

    A change of pile IS a change of verdict, so it records one — state and label
    move in a SINGLE transaction and can never disagree. Two browser writes
    could not be made atomic, and if the label write were the one that failed
    the pile would say "kept" while the model went on believing "dropped", with
    no reconciliation path. This is only safe because preference/train.py keeps
    one label per clip: the derived row supersedes the previous verdict instead
    of stacking a contradiction on top of it.

    An unchanged state is a total no-op — no row, no task, no activation event —
    which is what makes a double-click or an offline replay harmless.

    No budget or kill-switch dependency: organising your own clips must keep
    working at zero balance and during an LLM/render incident.
    """
    clip = await get_owned(session, Clip, clip_id, creator.id, detail="Clip not found")

    if clip.triage == body.triage:
        return {"id": str(clip_id), "triage": clip.triage.value}

    action = _TRIAGE_TO_ACTION[body.triage]
    # Checked BEFORE the commit so the row being written is not counted by the
    # existence query — same ordering as submit_feedback above.
    is_activation = action in _KEEP_ACTIONS and await _is_first_keep(session, creator.id)

    clip.triage = body.triage
    # Carries no tags or note, so distill_style_prefs (which selects only rows
    # where feedback_tags or feedback_note is non-null) cannot see it and its
    # STYLE_DISTILL_MIN_NEW debounce cannot be tripped by pile-shuffling.
    session.add(ClipFeedback(clip_id=clip_id, creator_id=creator.id, action=action))
    await session.commit()

    if is_activation:
        from event_log import record_event_nowait

        record_event_nowait(
            source="backend",
            event="clip_kept",
            creator_id=creator.id,
            extra={"action": action.value},
        )

    from worker.tasks import enqueue_retrain

    await enqueue_retrain(creator.id)
    return {"id": str(clip_id), "triage": body.triage.value}


@router.post(
    "/{clip_id}/trim-render",
    status_code=202,
    response_model=TaskQueuedOut,
    # Kill switch (Issue 284): 503 when the render_intake flag is off.
    dependencies=[Depends(require_flag("render_intake")), Depends(require_budget)],
)
@limiter.limit("20/hour", key_func=creator_key)
@limiter.limit(RENDER_DAILY_LIMIT, key_func=creator_key)
async def trim_render(
    request: Request,
    clip_id: uuid.UUID,
    body: TrimRenderIn,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Queue a re-render of the clip trimmed to ``[trim_start_s, trim_end_s]``.

    The trim window is inverted into cut segments for the existing
    ``edit_clip`` worker (which re-encodes from ``render_uri`` — trims keep
    working after the retention purge nulls the source video). The result
    lands in ``Clip.cleaned_render_uri``; the client confirms via the
    existing ``POST /clips/{clip_id}/clean/confirm`` swap.
    """
    from clip_engine.edits import CutValidationError, validate_user_cuts

    await check_positive_balance(creator.id, session)

    clip = await get_owned(session, Clip, clip_id, creator.id, detail="Clip not found")
    if not clip.render_uri:
        raise HTTPException(status_code=400, detail="Clip has not been rendered yet")
    # Mirror /cuts: refuse while a cleaned/edited artifact is pending, else
    # the worker idempotency probe silently drops this edit.
    if clip.cleaned_render_uri:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "pending_clean_or_edit",
                "message": "Confirm or discard the pending cleaned/edited version first.",
            },
        )

    clip_duration_s = _clip_duration_s(clip)
    # Right-edge tolerance of one frame matches validate_user_cuts' clamp.
    if body.trim_end_s > clip_duration_s + MIN_KEEP_SEGMENT_S:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "out_of_bounds",
                "message": (
                    f"trim_end_s ({body.trim_end_s}) is past the clip duration "
                    f"({clip_duration_s:.2f}s)"
                ),
            },
        )

    # Invert the trim window into cut segments: drop everything before
    # trim_start_s and after trim_end_s. Edges narrower than one frame
    # (MIN_KEEP_SEGMENT_S) are skipped so sub-frame float noise from the UI
    # doesn't emit a zero-width cut.
    cuts: list[list[float]] = []
    if body.trim_start_s >= MIN_KEEP_SEGMENT_S:
        cuts.append([0.0, body.trim_start_s])
    if clip_duration_s - body.trim_end_s >= MIN_KEEP_SEGMENT_S:
        cuts.append([body.trim_end_s, clip_duration_s])
    if not cuts:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "trim_noop",
                "message": "Trim covers the full clip — nothing to remove.",
            },
        )

    try:
        validate_user_cuts(
            [(c[0], c[1]) for c in cuts],
            clip_duration_s=clip_duration_s,
        )
    except CutValidationError as exc:
        raise HTTPException(
            status_code=422, detail={"code": exc.code, "message": str(exc)}
        ) from exc

    from worker.tasks import edit_clip as edit_task

    # SSE stream key is the clip id, not the Celery task id (sibling
    # convention — see /clips/{id}/cuts).
    task, stream_url = await enqueue_stream_task(
        edit_task,
        str(clip_id),
        cuts,
        creator_id=str(creator.id),
        stream_key=str(clip_id),
        log_label="trim-render",
    )

    return {
        "task_id": task.id,
        "status": "queued",
        "stream_url": stream_url,
    }
