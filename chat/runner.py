"""Agentic streaming loop for the Pro chatbot (Issue 152).

One user message → a manual agentic loop (the SDK-documented pattern for
client-side tools — see /claude-api python/claude-api/tool-use.md):

    stream → get_final_message() → if stop_reason == "tool_use", run the
    creator-scoped tools locally, append tool_result, loop.

Each blocking streamed LLM round-trip runs in ``asyncio.to_thread`` (the sync
Anthropic client blocks); tool execution stays in async land so it can touch the
DB. ``text_delta``s are forwarded to the SSE stream as they arrive; tool-call
JSON is not streamed to the user.

Margin/runaway guards (DECISIONS 2026-06-17): tool rounds capped at
``CHAT_MAX_TOOL_ITERATIONS`` (the final round forces ``tools=None`` so the model
must answer in text), output capped at ``CHAT_MAX_TOKENS``, history truncated by
the caller. Token usage is summed across the whole turn and logged.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

import httpx
from anthropic import Anthropic
from sqlalchemy.ext.asyncio import AsyncSession

from chat.prompt import build_system
from chat.tools import TOOLS, execute_tool
from config import settings
from observability import record_llm_tokens
from worker.anthropic_stream import stream_message
from worker.progress import aemit

logger = logging.getLogger(__name__)

# Module-level singleton (Issue 37 lifecycle rule). Mirrors analysis/brief.py.
_ANTHROPIC = Anthropic(
    api_key=settings.ANTHROPIC_API_KEY,
    timeout=httpx.Timeout(120.0, connect=10.0),
    max_retries=2,
)


def _text_of(message: Any) -> str:
    blocks = [b.text for b in message.content if getattr(b, "type", None) == "text"]
    return "\n".join(t for t in blocks if t).strip()


async def run_chat_turn(
    task_id: str,
    creator_id: uuid.UUID,
    channel_title: str | None,
    history: list[dict[str, Any]],
    session: AsyncSession,
) -> tuple[str, dict[str, int]]:
    """Run one assistant turn, streaming tokens to ``task:{task_id}:events``.

    ``history`` is the conversation as Anthropic message params, ending with the
    new user message. Returns ``(final_text, usage)`` where usage sums every
    round-trip in the turn.
    """
    system = build_system(channel_title)
    messages = list(history)
    total = {"input_tokens": 0, "output_tokens": 0, "cache_read": 0, "cache_creation": 0}
    final_text = ""

    max_iters = settings.CHAT_MAX_TOOL_ITERATIONS
    client = _ANTHROPIC.with_options(timeout=120.0)

    for i in range(max_iters + 1):
        # The final allowed round forces a text answer (no tools) so the loop
        # can't run past the cap with a dangling tool_use.
        tools = None if i == max_iters else TOOLS
        message, usage = await asyncio.to_thread(
            stream_message,
            client,
            task_id,
            model=settings.ANTHROPIC_MODEL,
            max_tokens=settings.CHAT_MAX_TOKENS,
            system=system,
            messages=messages,
            tools=tools,
        )
        for k in total:
            total[k] += usage.get(k, 0)

        if getattr(message, "stop_reason", None) != "tool_use":
            final_text = _text_of(message)
            break

        # Persist the assistant's tool-use turn, then execute each tool scoped to
        # this creator and feed results back.
        messages.append({"role": "assistant", "content": message.content})
        tool_results = []
        for block in message.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            await aemit(task_id, "step", label=f"tool:{block.name}", stage="chat")
            result_str, failed = await execute_tool(block.name, block.input, creator_id, session)
            # is_error: true gives Claude a documented semantic signal to recover
            # gracefully rather than treating the error JSON as successful data.
            # (Anthropic tool-use handle-tool-calls docs, fetched 2026-06-23; Issue 222)
            tool_result: dict = {
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result_str,
            }
            if failed:
                tool_result["is_error"] = True
            tool_results.append(tool_result)
        messages.append({"role": "user", "content": tool_results})

    logger.info(
        "chat turn tokens creator=%s in=%d cache_read=%d out=%d",
        creator_id,
        total["input_tokens"],
        total["cache_read"],
        total["output_tokens"],
    )

    from datetime import UTC, datetime

    from billing.ledger import _estimate_cost_usd, increment_usage

    try:
        cost = _estimate_cost_usd(
            total["input_tokens"],
            total["output_tokens"],
            settings.COST_PER_MTOK_IN_SONNET,
            settings.COST_PER_MTOK_OUT_SONNET,
            cache_read_tokens=total.get("cache_read", 0),
            cache_creation_tokens=total.get("cache_creation", 0),
        )
        await increment_usage(
            session,
            creator_id,
            datetime.now(UTC).strftime("%Y-%m"),
            total["input_tokens"],
            total["output_tokens"],
            cost,
        )
    except Exception as _exc:  # noqa: BLE001 — best-effort; never block chat
        logger.warning("chat usage ledger write failed creator=%s: %s", creator_id, _exc)

    record_llm_tokens(
        provider="anthropic",
        model=settings.ANTHROPIC_MODEL,
        input_tokens=total["input_tokens"],
        output_tokens=total["output_tokens"],
        cache_read_tokens=total.get("cache_read", 0),
        cache_creation_tokens=total.get("cache_creation", 0),
    )
    return final_text, total
