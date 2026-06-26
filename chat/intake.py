"""Chat-driven onboarding intake (Issue 96).

A guided, conversational alternative to the OnboardingIdentity wizard form: the
creator answers a few short questions and the model proposes a populated identity
profile for them to review and confirm.

**Non-streaming by design** (DECISIONS 2026-06-24). Each turn is one short
question — well under the request timeout — so the SSE/Celery streaming stack the
Pro chat uses (``chat/runner.py``) would be overkill. A plain request/response
turn keeps the feature self-contained and reuses the existing identity-write
endpoint for the confirm step.

**Security — this is a prompt-injection surface.** The creator's free-text
answers are UNTRUSTED. Defences:
  * The system prompt carries the verbatim ``UNTRUSTED_CONTENT_POLICY`` +
    ``HONESTY_CONSTRAINT``.
  * The model can only *propose* a profile via the ``propose_profile`` tool — it
    is never written from here.
  * Every proposed profile is run through the SAME ``dna.identity.validate_*``
    functions the wizard uses, so an unknown niche id or an over-length field is
    rejected regardless of what the model emitted.
  * The actual write happens only when the creator confirms, via the existing
    ``POST /creators/me/identity`` (which validates again). A manipulated model
    therefore cannot write invalid or unauthorized data.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx
from anthropic import AsyncAnthropic, APIConnectionError, APIStatusError, RateLimitError

from chat.prompt import HONESTY_CONSTRAINT
from config import settings
from dna import identity as identity_module
from knowledge.util import UNTRUSTED_CONTENT_POLICY
from observability import record_llm_tokens
from youtube.categories import NICHE_OPTIONS

logger = logging.getLogger(__name__)

# Module-level singleton (Issue 37 lifecycle rule). Mirrors clip_engine/scoring.py.
_ANTHROPIC = AsyncAnthropic(
    api_key=settings.ANTHROPIC_API_KEY,
    timeout=httpx.Timeout(60.0, connect=10.0),
    max_retries=2,
)

# Runaway guard: cap creator turns per intake so a creator (or a manipulated
# model) can't loop the LLM indefinitely. One "turn" = one user + one assistant.
MAX_INTAKE_TURNS = 12

_NICHE_LINES = "\n".join(f'  - "{o["id"]}" = {o["label"]}' for o in NICHE_OPTIONS)

# Strict-schema tool: the model signals "ready to propose" by calling this; its
# input mirrors the CreatorIdentity columns the wizard collects.
PROPOSE_PROFILE_TOOL: dict = {
    "name": "propose_profile",
    "description": (
        "Propose a populated creator-identity profile for the creator to review and "
        "confirm. Call this ONLY once you have at least the creator's niche(s) and a "
        "one-sentence audience description. Never call it on your first message."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "niches": {
                "type": "array",
                "items": {"type": "string"},
                "description": '1-3 YouTube category IDs from the allowed list (the numeric id, e.g. "20").',
            },
            "audience_summary": {
                "type": "string",
                "description": "One sentence describing who the audience is.",
            },
            "content_pillars": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional: 2-5 recurring themes or series.",
            },
            "tone_tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional: short tone words, e.g. 'warm', 'no-nonsense'.",
            },
            "hard_nos": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional: things the creator will never do.",
            },
            "mission": {
                "type": "string",
                "description": "Optional: one line on what they're building.",
            },
        },
        "required": ["niches", "audience_summary"],
        "additionalProperties": False,
    },
}

INTAKE_SYSTEM = f"""\
{UNTRUSTED_CONTENT_POLICY}
You are the CreatorClip onboarding guide. Your job is a short, friendly intake: \
help a YouTube creator describe their channel so AutoClip can tailor clip scoring \
to them. You speak only to this one creator and have no access to anyone else's data.

Honesty constraint (load-bearing — applies to every message):
{HONESTY_CONSTRAINT}

How to run the intake:
- Ask ONE question at a time. Keep each message to 1-2 short, plain, warm sentences.
- Cover, in order: (1) what the channel is about / its niche, (2) who the audience is, \
then OPTIONALLY (3) tone, (4) anything they'll never do (hard-nos), (5) what they're building.
- The creator may skip any optional question — never pressure them.
- Niche must be one or more of these YouTube category IDs. Use the numeric id, not the label:
{_NICHE_LINES}
- Map what the creator says to the closest 1-3 ids above. If genuinely unsure, ask one brief \
clarifying question rather than guessing.
- As soon as you have at least a niche and a one-sentence audience — and the creator isn't \
adding more — call the propose_profile tool with everything gathered so far. Do NOT call \
propose_profile on your first message.
- Never promise or imply views, growth, or virality. This intake sharpens fit, not reach."""


def _validate_proposal(raw: dict[str, Any]) -> dict[str, Any]:
    """Run a model-proposed profile through the SAME validators the wizard uses.

    Returns a cleaned dict on success; raises ``ValueError`` on invalid input
    (the caller turns that into a tool_result error so the model can self-correct).
    """
    return {
        "niches": identity_module.validate_niches(raw.get("niches") or []),
        "audience_summary": identity_module.validate_text(
            raw.get("audience_summary") or "",
            max_chars=identity_module.MAX_AUDIENCE_CHARS,
            label="audience_summary",
        ),
        "content_pillars": identity_module.validate_list(
            raw.get("content_pillars"), label="content_pillars"
        ),
        "tone_tags": identity_module.validate_list(raw.get("tone_tags"), label="tone_tags"),
        "hard_nos": identity_module.validate_list(raw.get("hard_nos"), label="hard_nos"),
        "mission": identity_module.validate_optional_text(
            raw.get("mission"), max_chars=identity_module.MAX_MISSION_CHARS, label="mission"
        ),
    }


def _text_of(message: Any) -> str:
    parts = [b.text for b in message.content if getattr(b, "type", None) == "text" and b.text]
    return "\n".join(parts).strip()


async def run_intake_turn(creator_id: uuid.UUID, history: list[dict[str, Any]]) -> dict[str, Any]:
    """Run one assistant intake turn.

    ``history`` is the conversation so far as Anthropic message params, ending
    with the latest creator (user) message. Returns
    ``{"reply": str, "proposal": dict | None}`` — ``proposal`` is the VALIDATED
    profile when the model is ready to propose. The caller NEVER writes it; the
    creator confirms via ``POST /creators/me/identity``.
    """
    # Runaway guard — bail to the form rather than loop the LLM.
    if len(history) > MAX_INTAKE_TURNS * 2:
        return {
            "reply": "Let's wrap up here — confirm or tweak your details on the form below.",
            "proposal": None,
        }

    # Typed as list[Any]: the Anthropic SDK's create() params are strict TypedDicts,
    # and these are built/mutated as plain dicts (history from the router, appended
    # tool_result blocks) — Any keeps mypy green without per-line ignores.
    messages: list[Any] = list(history)
    system: list[Any] = [
        {"type": "text", "text": INTAKE_SYSTEM, "cache_control": {"type": "ephemeral"}}
    ]
    tools: list[Any] = [PROPOSE_PROFILE_TOOL]
    total_in = total_out = 0
    proposal: dict[str, Any] | None = None
    reply = ""

    # At most one validation-correction round, so a bad propose_profile can
    # self-correct once without looping.
    for attempt in range(2):
        try:
            message = await _ANTHROPIC.messages.create(
                model=settings.ANTHROPIC_MODEL_INTAKE,
                max_tokens=settings.CHAT_MAX_TOKENS,
                system=system,
                messages=messages,
                tools=tools,
            )
        except (RateLimitError, APIStatusError, APIConnectionError) as exc:
            logger.error(
                "intake_turn LLM error creator=%s exc_type=%s",
                creator_id, type(exc).__name__,
            )
            raise
        usage = getattr(message, "usage", None)
        total_in += getattr(usage, "input_tokens", 0) or 0
        total_out += getattr(usage, "output_tokens", 0) or 0

        tool_use = next(
            (
                b
                for b in message.content
                if getattr(b, "type", None) == "tool_use"
                and getattr(b, "name", None) == "propose_profile"
            ),
            None,
        )
        if tool_use is None:
            reply = _text_of(message)
            break

        try:
            proposal = _validate_proposal(dict(getattr(tool_use, "input", {}) or {}))
            reply = _text_of(message) or (
                "Here's what I've got — review it and confirm, or tell me what to change."
            )
            break
        except ValueError as exc:
            if attempt == 0:
                # Feed the validation error back so the model fixes its proposal once.
                messages.append({"role": "assistant", "content": message.content})
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": getattr(tool_use, "id", ""),
                                "content": (
                                    f"Invalid profile: {exc}. Correct it and call "
                                    "propose_profile again, or ask a brief clarifying question."
                                ),
                                "is_error": True,
                            }
                        ],
                    }
                )
                continue
            # Second failure — abandon the proposal, hand off to the form.
            reply = (
                _text_of(message)
                or "I couldn't quite pin that down — let's finish on the form below."
            )
            proposal = None
            break

    record_llm_tokens(
        provider="anthropic",
        model=settings.ANTHROPIC_MODEL,
        input_tokens=total_in,
        output_tokens=total_out,
    )
    logger.info(
        "intake turn creator=%s in=%d out=%d proposal=%s",
        creator_id,
        total_in,
        total_out,
        proposal is not None,
    )
    return {"reply": reply, "proposal": proposal}
