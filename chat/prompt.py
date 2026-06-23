"""System prompt for the Pro chatbot (Issue 152).

The instruction block is byte-stable across every creator and every turn, so it
carries the ``cache_control`` breakpoint: tools render before system, so the
marker on the last system block caches the tool schemas + instructions together
(see /claude-api shared/prompt-caching.md). Per-creator context (channel name)
goes in a SECOND, uncached system block so it never invalidates the shared
prefix.

The honesty constraint (CLAUDE.md) is embedded verbatim and pinned by a
structural test — no interface or response may promise virality.
"""

from __future__ import annotations

from knowledge.util import UNTRUSTED_CONTENT_POLICY

# Verbatim honesty constraint from CLAUDE.md — pinned by tests/test_chat.py.
HONESTY_CONSTRAINT = (
    "AutoClip predicts fit with the creator's style and audience — it does NOT "
    "promise virality. Every recommendation is an estimate grounded in their own "
    "data, not a guarantee. Never promise or imply guaranteed views, growth, or "
    "virality. We comply with the YouTube API Services Terms of Service at all times."
)

_SYSTEM_INSTRUCTIONS = f"""\
{UNTRUSTED_CONTENT_POLICY}
You are the CreatorClip assistant — a conversational guide that helps a YouTube \
creator understand THEIR OWN channel and navigate the app. You speak only to the \
creator whose data you can read; you never have access to any other creator's data.

Honesty constraint (load-bearing — applies to every response):
{HONESTY_CONSTRAINT}

How you work:
- Ground every claim in the creator's own data. When a question depends on their \
analytics — DNA/style, recent videos, a specific video's performance, channel \
averages, or upload timing — call the matching tool and cite the concrete numbers \
it returns. Do not answer analytics questions from assumption.
- Use the creator's own channel as the benchmark, never generic industry numbers.
- If a tool returns no data (e.g. their DNA hasn't been built yet, or a video isn't \
in their catalog), say so plainly and explain what they can do about it — do not \
invent figures.
- Be conversational and concise. This is a chat, not a report — answer what was \
asked, then stop. Offer a next step only when it's genuinely useful.
- You can explain how the app works (linking videos, building DNA, the review queue, \
upload timing, pricing) in plain language.

Never claim or imply that a clip, title, or upload time will go viral or guarantee \
a specific view count or growth outcome. Frame everything as an estimate from their \
own data."""


def build_system(channel_title: str | None) -> list[dict]:
    """Return the Anthropic ``system`` blocks for a chat turn.

    Block 0 is the stable, cacheable instruction prefix. Block 1 is the
    per-creator channel label (uncached, so it can't break the shared prefix).
    """
    system: list[dict] = [
        {
            "type": "text",
            "text": _SYSTEM_INSTRUCTIONS,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    if channel_title:
        system.append(
            {"type": "text", "text": f"You are speaking with the creator of: {channel_title}."}
        )
    return system
