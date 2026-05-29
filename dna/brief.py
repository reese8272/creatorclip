"""
Generate a plain-language Creator Brief via Claude.

Prompt structure (Issue 69): a STATIC instruction block carries the
``cache_control: ephemeral`` breakpoint; the per-creator corpus is a SEPARATE,
uncached system block after it. This is the correct split, but note the static
prefix is well below Sonnet 4.6's 2048-token minimum cacheable prefix, so the
cache does not actually engage for this low-frequency call — see docs/DECISIONS.md.
"""

import json
import logging

import httpx
from anthropic import Anthropic

from config import settings

logger = logging.getLogger(__name__)

_ANTHROPIC = Anthropic(
    api_key=settings.ANTHROPIC_API_KEY,
    timeout=httpx.Timeout(60.0, connect=10.0),
    max_retries=2,
)

_DISCLAIMER = (
    "\n\n---\n"
    "*These insights are estimates grounded in your own channel data. "
    "CreatorClip predicts fit with your style and audience — it does not promise virality.*"
)

# Static instruction block — no per-creator data, so it is identical across all
# calls and carries the cache_control breakpoint. (Issue 69)
_SYSTEM_INSTRUCTIONS = """\
You are an expert YouTube channel analyst helping a creator understand their channel's performance patterns.

Analyse the creator's performance data (provided in the next block) and write a concise, actionable Creator Brief in plain Markdown.

Structure it with exactly these five sections:
1. **Channel Signature** — 2-3 sentences on what makes this creator's best content work
2. **What's Driving Views** — top 3 patterns from their highest-performing videos (hook styles, energy moments, optimal length)
3. **Where to Improve** — 2 specific, data-backed opportunities based on bottom-performer patterns
4. **Optimal Clip Profile** — typical clip length, best source region of the video, upload rhythm
5. **Shorts Strategy** — if Shorts data is available, what's working vs not

Be specific: reference actual video titles where relevant. Keep total length under 500 words.
Phrase all predictions as likelihood estimates grounded in their data — never promise virality."""


def generate_brief(patterns: dict, channel_title: str) -> str:
    """
    Call Claude to synthesise a Creator Brief from computed patterns.

    Returns brief_text with the honesty disclaimer appended.
    Raises RuntimeError if Claude returns no text block.
    """
    corpus = json.dumps(
        {"channel": channel_title, "performance_data": patterns},
        indent=2,
        default=str,
    )

    response = _ANTHROPIC.messages.create(
        model=settings.ANTHROPIC_MODEL,
        max_tokens=2000,
        system=[
            # Stable prefix — carries the cache breakpoint.
            {
                "type": "text",
                "text": _SYSTEM_INSTRUCTIONS,
                "cache_control": {"type": "ephemeral"},
            },
            # Volatile per-creator data — AFTER the breakpoint, never cached.
            {"type": "text", "text": f"CREATOR PERFORMANCE DATA:\n{corpus}"},
        ],
        messages=[
            {
                "role": "user",
                "content": (f"Generate the Creator Brief for '{channel_title}'."),
            }
        ],
    )

    logger.info(
        "dna_brief tokens: in=%d cached_read=%d cached_write=%d out=%d",
        response.usage.input_tokens,
        getattr(response.usage, "cache_read_input_tokens", 0),
        getattr(response.usage, "cache_creation_input_tokens", 0),
        response.usage.output_tokens,
    )

    text_blocks = [b for b in response.content if b.type == "text"]
    if not text_blocks:
        raise RuntimeError("Claude returned no text block in DNA brief generation")

    # Final text block is the answer (consistent with the web_search path). (Issue 69)
    return text_blocks[-1].text + _DISCLAIMER
