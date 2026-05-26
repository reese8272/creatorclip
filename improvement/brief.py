"""
Generate a content-improvement brief using Claude with the web_search tool.

The creator's own analytics data is passed as a prompt-cached prefix; Claude
then calls web_search to pull current algorithm guidance and synthesises
actionable, data-grounded recommendations.

The honesty disclaimer is always appended by Python — never left to the LLM.
"""

import json
import logging

from anthropic import Anthropic

from config import settings

logger = logging.getLogger(__name__)

_DISCLAIMER = (
    "\n\n---\n"
    "*These recommendations are estimates grounded in your channel data and publicly "
    "available information. CreatorClip does not promise virality or specific growth outcomes.*"
)

_SYSTEM = """\
You are a YouTube growth strategist with access to real-time web search.

The creator's analytics and DNA profile data are provided below. Use them as your
primary source of truth about what works for this specific channel.

Then use the web_search tool to find current YouTube algorithm guidance, recent
platform changes, and trending content strategies relevant to this creator's niche.

Synthesise both into 3–5 specific, actionable improvements. For each:
- State the recommendation clearly
- Cite the creator's data (engagement rate, retention, hook performance) as evidence
- Note any current algorithm factor that makes this timely
- Estimate the likely impact (high / medium / low) — never promise virality

Keep total length under 600 words. Phrase predictions as likelihood estimates, not guarantees.

CREATOR ANALYTICS DATA:
{analytics_json}
"""


_anthropic_client: Anthropic | None = None


def _get_client() -> Anthropic:
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _anthropic_client


def generate_improvement_brief(
    channel_title: str,
    analytics: dict,
    dna_brief: str | None = None,
) -> str:
    """
    Call Claude with web_search to generate a data + research grounded improvement brief.
    Returns brief_text with disclaimer appended.
    """
    payload = {"channel": channel_title, "analytics": analytics}
    if dna_brief:
        payload["dna_summary"] = dna_brief[:1000]  # cap so system block stays cacheable

    analytics_json = json.dumps(payload, indent=2, default=str)
    system_text = _SYSTEM.format(analytics_json=analytics_json)

    response = _get_client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        system=[
            {
                "type": "text",
                "text": system_text,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[
            {
                "role": "user",
                "content": (
                    f"Generate the improvement brief for '{channel_title}'. "
                    "Search for the most current YouTube algorithm guidance relevant to this channel's niche "
                    "before writing your recommendations."
                ),
            }
        ],
    )

    logger.info(
        "improvement_brief tokens: in=%d cached_read=%d cached_write=%d out=%d",
        response.usage.input_tokens,
        getattr(response.usage, "cache_read_input_tokens", 0),
        getattr(response.usage, "cache_creation_input_tokens", 0),
        response.usage.output_tokens,
    )

    text_blocks = [b for b in response.content if b.type == "text"]
    if not text_blocks:
        raise RuntimeError("Claude returned no text in improvement brief generation")

    return text_blocks[0].text + _DISCLAIMER
