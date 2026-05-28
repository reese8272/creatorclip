"""
Generate a plain-language Creator Brief via Claude.

The DNA corpus is embedded as a prompt-cached system block so the same
cache hit is reused by the clip scorer (Issue 8) for the same profile version.
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

_SYSTEM_TEMPLATE = """\
You are an expert YouTube channel analyst helping a creator understand their channel's performance patterns.

Analyse the creator's performance data below and write a concise, actionable Creator Brief in plain Markdown.

Structure it with exactly these five sections:
1. **Channel Signature** — 2-3 sentences on what makes this creator's best content work
2. **What's Driving Views** — top 3 patterns from their highest-performing videos (hook styles, energy moments, optimal length)
3. **Where to Improve** — 2 specific, data-backed opportunities based on bottom-performer patterns
4. **Optimal Clip Profile** — typical clip length, best source region of the video, upload rhythm
5. **Shorts Strategy** — if Shorts data is available, what's working vs not

Be specific: reference actual video titles where relevant. Keep total length under 500 words.
Phrase all predictions as likelihood estimates grounded in their data — never promise virality.

---
CREATOR PERFORMANCE DATA:
{corpus}"""


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
    system_text = _SYSTEM_TEMPLATE.format(corpus=corpus)

    response = _ANTHROPIC.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        system=[
            {
                "type": "text",
                "text": system_text,
                "cache_control": {"type": "ephemeral"},
            }
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

    return text_blocks[0].text + _DISCLAIMER
