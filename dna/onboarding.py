"""Setup-step resolver — single source of truth for "what should this
creator do next?".

Centralizes the inference the frontend was previously doing by fan-out
across ``/auth/me`` + ``/creators/me/data-gate`` + ``/creators/me/dna``
+ ``/videos`` + ``/billing/balance``. Called from ``/auth/me`` and
``/creators/me`` so a single fetch resolves the next-step CTA.

The Creator's ``onboarding_state`` enum is the fast-path; the resolver
issues at most ONE extra query to disambiguate ambiguous states
(``connected`` needs a data-gate readiness check; ``active`` needs to
know whether any clip-track videos exist).

``awaiting_data`` is RESERVED — it was never written by any code path
(``youtube/oauth.py:179`` sets ``connected``; advances go
``connected → dna_pending`` via ``dna/profile.py:83`` and
``dna_pending → active`` via ``dna/profile.py:135``). The enum value is
kept in ``models.OnboardingState`` to avoid a Postgres enum migration;
any creator row with this state is treated identically to ``connected``
in the resolver. Issue 235, DECISIONS 2026-06-23.

DECISIONS 2026-06-08 — Onboarding state aggregation.
"""

from __future__ import annotations

import uuid
from typing import Literal, TypedDict

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Creator, OnboardingState, Video, VideoOrigin
from youtube.analytics import check_data_gate

SetupStepName = Literal[
    "sync_catalog",
    "build_dna",
    "confirm_dna",
    "link_first_video",
    "complete",
]

NextActionType = Literal["navigate", "open_form", "wait"]


class SetupStep(TypedDict):
    """Shape returned by ``resolve_setup_step``.

    Matches ``routers.creators.SetupStepOut`` field-for-field — the
    TypedDict is for callers that want static checking without importing
    the Pydantic model and dragging FastAPI into ``dna/``.
    """

    step: SetupStepName
    label: str
    next_action_type: NextActionType
    next_action_url: str | None
    progress_index: int
    progress_total: int


_PROGRESS_TOTAL = 4

# Ordered progress map. ``complete`` shares index 4 with ``link_first_video``
# because both indicate the creator is past DNA confirmation — the difference
# is whether the dashboard has anything to clip yet. The progress bar is the
# same length either way.
_PROGRESS_INDEX: dict[SetupStepName, int] = {
    "sync_catalog": 1,
    "build_dna": 2,
    "confirm_dna": 3,
    "link_first_video": 4,
    "complete": 4,
}


def _step(
    name: SetupStepName,
    label: str,
    action: NextActionType,
    url: str | None,
) -> SetupStep:
    return {
        "step": name,
        "label": label,
        "next_action_type": action,
        "next_action_url": url,
        "progress_index": _PROGRESS_INDEX[name],
        "progress_total": _PROGRESS_TOTAL,
    }


async def _has_clip_track_videos(session: AsyncSession, creator_id: uuid.UUID) -> bool:
    """True when the creator has added at least one non-catalog video
    (i.e. a linked or uploaded video that shows on the dashboard — catalog-only
    DNA references are excluded, matching ``routers/videos.py::list_videos``).
    Issue 139: switched from the ``source_uri IS NOT NULL`` heuristic to the
    ``origin`` discriminator so a creator who only *links* a video (no stored
    media yet) still progresses past the ``link_first_video`` onboarding step.
    """
    result = await session.execute(
        select(func.count(Video.id)).where(
            Video.creator_id == creator_id,
            Video.origin != VideoOrigin.catalog,
        )
    )
    return (result.scalar_one() or 0) > 0


async def resolve_setup_step(creator: Creator, session: AsyncSession) -> SetupStep:
    """Return the canonical next-step for ``creator``.

    Reads ``creator.onboarding_state`` first and issues at most one
    follow-up query. Total per-call cost on the hot path (``active``
    creators) is one ``COUNT(*)`` against ``videos`` — indexed, cheap.

    ``awaiting_data`` is treated as ``connected`` — it is a reserved enum
    value that is never written by any current code path (Issue 235).
    """
    state = creator.onboarding_state

    # ``awaiting_data`` is RESERVED (never written; see module docstring).
    # Treat it identically to ``connected`` so any legacy row that somehow
    # carries the value resolves correctly instead of falling through to the
    # active-state branch.
    if state in (OnboardingState.connected, OnboardingState.awaiting_data):
        gate = await check_data_gate(session, creator.id)
        if not gate["ready"]:
            return _step(
                "sync_catalog",
                "Sync your channel so AutoClip can learn your style",
                "navigate",
                "/app/onboarding",
            )
        return _step(
            "build_dna",
            "Build your Creator DNA from your channel history",
            "navigate",
            "/app/onboarding",
        )

    if state == OnboardingState.dna_pending:
        return _step(
            "confirm_dna",
            "Review and confirm your Creator DNA brief",
            "navigate",
            "/app/profile",
        )

    # state == OnboardingState.active
    if not await _has_clip_track_videos(session, creator.id):
        return _step(
            "link_first_video",
            "Link your first video to generate clips",
            "open_form",
            "/app/dashboard",
        )
    return _step(
        "complete",
        "You're set — link a video to make new clips.",
        "navigate",
        "/app/dashboard",
    )
