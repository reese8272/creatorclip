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
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_creator
from billing.ledger import check_positive_balance
from billing.spend_guard import require_budget
from clip_engine.edits import MIN_KEEP_SEGMENT_S
from db import get_session
from flags import require_flag
from limiter import RENDER_DAILY_LIMIT, creator_key, limiter
from models import Clip, ClipFeedback, Creator, FeedbackAction
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
    id: str
    action: str


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
    """Duration of the rendered clip in CLIP-RELATIVE seconds.

    Origin is ``setup_start_s`` when set, else ``start_s`` — the timebase
    shared by /transcript, /cuts, /feedback and /trim-render. Both trim
    routes derive their bounds from this one helper so they cannot drift.
    """
    origin = clip.setup_start_s if clip.setup_start_s is not None else clip.start_s
    return clip.end_s - origin


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
    """Record a feedback action for a clip."""
    clip = await get_owned(session, Clip, clip_id, creator.id, detail="Clip not found")

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
    # The task self-debounces (no-op without new trainable labels), so enqueuing
    # on every feedback write is cheap. (Issue 60)
    from worker.tasks import retrain_preference

    await asyncio.to_thread(retrain_preference.delay, str(creator.id))

    return {"id": str(feedback.id), "action": feedback.action.value}


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
