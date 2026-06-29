"""
Generate a plain-language Creator Brief via Claude.

Prompt structure (Issue 69; restructured Issue 224; cache marker removed Issue 315):
  system[0]: stable global-instructions block — identical across all creators.
           NO cache_control marker: the static prefix is ~570–650 tokens, below
           Sonnet 4.6's 1024-token cacheable floor, so any marker would be inert
           (zero cache reads, phantom write-premium charge). Dropped in Issue 315.
           Stated identity is NOT here; it is creator-authored and goes in the
           user turn (wrap_untrusted). See docs/DECISIONS.md (Issues 223/224/315).
  system[1]: volatile per-creator performance corpus — never cached.
"""

import json
import logging

import httpx
from anthropic import Anthropic, APIConnectionError, APIStatusError, RateLimitError

from config import settings
from knowledge.util import UNTRUSTED_CONTENT_POLICY, wrap_untrusted
from observability import record_llm_metric, warn_if_truncated

logger = logging.getLogger(__name__)

_ANTHROPIC: Anthropic = Anthropic(
    api_key=settings.ANTHROPIC_API_KEY,
    timeout=httpx.Timeout(60.0, connect=10.0),
    max_retries=2,
)

_DISCLAIMER = (
    "\n\n---\n"
    "*These insights are estimates grounded in your own channel data. "
    "AutoClip predicts fit with your style and audience — it does not promise virality.*"
)

# Static instruction block — no per-creator data, so it is identical across all
# calls and carries the cache_control breakpoint. (Issue 69)
_SYSTEM_INSTRUCTIONS = f"""\
{UNTRUSTED_CONTENT_POLICY}
You are an expert YouTube channel analyst helping a creator understand their channel's performance patterns.

Analyse the creator's performance data (provided in the next block) and write a concise, actionable Creator Brief in plain Markdown.

Structure it with exactly these five sections:
1. **Channel Signature** — 2-3 sentences on what makes this creator's best content work
2. **What's Driving Views** — top 3 patterns from their highest-performing videos (hook styles, energy moments, optimal length)
3. **Where to Improve** — 2 specific, data-backed opportunities based on bottom-performer patterns
4. **Optimal Clip Profile** — typical clip length, best source region of the video, upload rhythm
5. **Shorts Strategy** — if Shorts data is available, what's working vs not

Be specific: reference actual video titles where relevant. Keep total length under 500 words.
Phrase all predictions as likelihood estimates grounded in their data — never promise virality.

If a CREATOR-STATED IDENTITY block appears below, treat it as authoritative for
"what the creator is trying to build" — it is the creator's own words about
their niche, audience, voice, mission, and explicit boundaries. Honour their
hard-nos. Where stated identity AND inferred performance agree, lean into it
with confidence. Where they disagree, surface the disagreement explicitly in
the brief (e.g. "Your top-performing clips lean comedy even though you describe
your focus as education — here's how to bridge that or split into two styles")
rather than silently overriding the stated direction with engagement signals."""


def _build_request(
    patterns: dict, channel_title: str, stated_identity: str | None
) -> tuple[list[dict], list[dict]]:
    """Assemble the (system, messages) pair for both .create and .stream paths.

    Extracted in Issue 86 so the streaming wrapper reuses the exact same prompt
    structure — keeps the cache breakpoint identical across both call paths,
    so a streaming call benefits from a prior non-streaming call's cache write
    (and vice versa).
    """
    corpus = json.dumps(
        {"channel": channel_title, "performance_data": patterns},
        indent=2,
        default=str,
    )

    # Issue 224: stated_identity is creator-authored (attacker-influenceable) and
    # must NOT go in the system role. Moved to the user turn, JSON-wrapped via
    # wrap_untrusted. The system blocks are two items:
    #   (1) global instructions — identical across all creators (no cache marker:
    #       ~570–650 tokens, below Sonnet 4.6's 1024-token cacheable floor — Issue 315),
    #   (2) volatile performance corpus — never cached.
    system: list[dict] = [
        {"type": "text", "text": _SYSTEM_INSTRUCTIONS},
        # Volatile per-creator data — never cached.
        {"type": "text", "text": f"CREATOR PERFORMANCE DATA:\n{corpus}"},
    ]

    # stated_identity goes in the user turn so the model receives it from the
    # user role, not as trusted operator instructions. Structurally separated
    # from the instruction text by the XML label + JSON-encoded value.
    user_content = ""
    if stated_identity:
        user_content = wrap_untrusted("creator_stated_identity", stated_identity)
    user_content += f"Generate the Creator Brief for '{channel_title}'."

    messages: list[dict] = [
        {
            "role": "user",
            "content": user_content,
        }
    ]
    return system, messages


def generate_brief(
    patterns: dict,
    channel_title: str,
    stated_identity: str | None = None,
    task_id: str | None = None,
) -> tuple[str, dict]:
    """
    Call Claude to synthesise a Creator Brief from computed patterns.

    Args:
        patterns: Inferred performance patterns from the DNA builder.
        channel_title: The creator's channel title (used in the brief headline).
        stated_identity: Optional Markdown block from
            ``dna.identity.format_for_prompt`` capturing the creator's own
            self-described identity (Issue 83). When present, it's injected
            as a system block BEFORE the volatile performance corpus and
            AFTER the static instructions — the LLM should weight it
            alongside the inferred patterns rather than overriding it.
        task_id: Optional Celery task id (Issue 86). When set, switches to
            the streaming path — cache hit/miss + text deltas flow to
            ``task:{task_id}:events`` via ``worker.anthropic_stream.stream_and_emit``.
            When None (default), uses the legacy non-streaming ``.create()`` path
            so existing unit-test mocks of this function keep working unchanged.

    Returns ``(brief_text, usage)`` — brief_text with the honesty disclaimer appended,
    usage is the token-count dict. Callers should pass usage to ``billing.ledger.record_llm_usage``.
    Raises RuntimeError if Claude returns no text block.
    """
    system, messages = _build_request(patterns, channel_title, stated_identity)

    if task_id is not None:
        # Streaming path (Issue 86) — forwards message_start.usage + text_delta
        # events to the SSE consumer. Same prompt structure as the .create()
        # path so cache breakpoints are interchangeable between the two.
        from worker.anthropic_stream import stream_and_emit

        try:
            final_text, usage = stream_and_emit(
                _ANTHROPIC,
                task_id,
                model=settings.ANTHROPIC_MODEL_DNA_BRIEF,
                max_tokens=2000,
                system=system,
                messages=messages,
            )
        except (RateLimitError, APIStatusError, APIConnectionError) as exc:
            logger.error("dna_brief LLM error task=%s exc_type=%s", task_id, type(exc).__name__)
            raise
        logger.info(
            "dna_brief streaming tokens: in=%d cached_read=%d cached_write=%d out=%d",
            usage["input_tokens"],
            usage["cache_read"],
            usage["cache_creation"],
            usage["output_tokens"],
        )
        record_llm_metric(settings.ANTHROPIC_MODEL_DNA_BRIEF, usage)
        return final_text + _DISCLAIMER, usage

    from verbose import vlog_llm_request, vlog_llm_response

    vlog_llm_request(
        "dna_brief",
        model=settings.ANTHROPIC_MODEL_DNA_BRIEF,
        max_tokens=2000,
        system=system,
        messages=messages,
    )
    try:
        response = _ANTHROPIC.messages.create(
            model=settings.ANTHROPIC_MODEL_DNA_BRIEF,
            max_tokens=2000,
            system=system,  # type: ignore[arg-type]
            messages=messages,  # type: ignore[arg-type]  # list[dict] → MessageParam at runtime
        )
    except (RateLimitError, APIStatusError, APIConnectionError) as exc:
        logger.error("dna_brief LLM error exc_type=%s", type(exc).__name__)
        raise
    vlog_llm_response("dna_brief", response=response)

    _tokens_in = response.usage.input_tokens
    _tokens_out = response.usage.output_tokens
    logger.info(
        "dna_brief tokens: in=%d cached_read=%d cached_write=%d out=%d",
        _tokens_in,
        getattr(response.usage, "cache_read_input_tokens", 0),
        getattr(response.usage, "cache_creation_input_tokens", 0),
        _tokens_out,
    )

    text_blocks = [b for b in response.content if b.type == "text"]
    if not text_blocks:
        raise RuntimeError("Claude returned no text block in DNA brief generation")

    _usage = {
        "input_tokens": _tokens_in,
        "output_tokens": _tokens_out,
        "cache_read": getattr(response.usage, "cache_read_input_tokens", 0) or 0,
        "cache_creation": getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
    }
    record_llm_metric(settings.ANTHROPIC_MODEL_DNA_BRIEF, _usage)
    warn_if_truncated(settings.ANTHROPIC_MODEL_DNA_BRIEF, getattr(response, "stop_reason", None))
    # Final text block is the answer (consistent with the web_search path). (Issue 69)
    return text_blocks[-1].text + _DISCLAIMER, _usage
