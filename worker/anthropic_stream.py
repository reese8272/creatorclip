"""Stream Anthropic responses while emitting live progress events (Issue 86).

Wraps the sync Anthropic ``messages.stream(...)`` context manager so that:

* Cache hit/miss + input tokens are surfaced as an ``event: cache`` BEFORE
  the first generated token (via the ``message_start`` event's usage).
* Each ``text_delta`` is forwarded as ``event: token``.
* Each ``thinking_delta`` (extended thinking, post-SDK-bump) is forwarded as
  ``event: thinking`` — generic delta forwarding so any unknown delta types
  added by future SDK releases are silently dropped instead of crashing.
* The final text and a structured usage dict are returned to the caller.

Designed to be called from inside ``asyncio.to_thread`` in a Celery worker —
the Anthropic sync client blocks, so the progress emitter is sync too.

Per CLAUDE.md /claude-api guidance:
  - For 0.40, do not pass thinking={...} — the param shape isn't recognized.
    Enable thinking when the SDK is bumped in Issue 84.
  - `getattr` on every usage field is defensive: older SDK responses don't
    always populate cache_read_input_tokens / cache_creation_input_tokens
    (e.g. when caching wasn't engaged or for non-cacheable models).
  - Wrap each sync_emit call in try/except inside the loop so a Redis hiccup
    can't abort iteration before `get_final_message()` is reachable.
"""

from __future__ import annotations

import logging
from typing import Any

from observability import warn_if_truncated
from worker.progress import sync_emit

logger = logging.getLogger(__name__)


def stream_and_emit(
    client: Any,
    task_id: str,
    *,
    model: str,
    max_tokens: int,
    system: Any,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> tuple[str, dict[str, int]]:
    """Run a streamed Anthropic call, forwarding deltas as progress events.

    Returns
    -------
    (final_text, usage_dict)
        ``final_text`` is the last text block's text (matches the pattern used
        in the existing non-streaming ``dna/brief.py``). ``usage_dict`` has
        ``input_tokens``, ``output_tokens``, ``cache_read``, ``cache_creation``.

    The ``tools`` parameter (Wave-3 Fix A) is forwarded to
    ``client.messages.stream(...)`` when not None — needed for callers like
    ``improvement/brief.py`` that use ``web_search``. Tool-use blocks land on
    the same content stream as text; the FINAL text block of the response
    remains the synthesised answer (Issue-69 pattern), so we still return
    ``text_blocks[-1].text`` and don't need to handle ``tool_use`` deltas
    explicitly.

    The caller is responsible for emitting the terminal ``done`` / ``error``
    event after this returns — this function only forwards intra-stream events.
    """
    # Only forward `tools` to the SDK when the caller actually has tools to
    # pass. Older SDK shapes raise on `tools=None`, so we drop the kwarg
    # entirely in the no-tools case — matches `dna/brief.py`'s call shape.
    stream_kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
    }
    if tools is not None:
        stream_kwargs["tools"] = tools

    with client.messages.stream(**stream_kwargs) as stream:
        for event in stream:
            try:
                _forward_event(task_id, event)
            except Exception as exc:
                # Per /claude-api guidance: a hiccup forwarding one event must
                # NOT abort iteration — losing the final_text would be worse.
                logger.warning("stream_and_emit: forward failed task=%s err=%s", task_id, exc)
        final = stream.get_final_message()

    warn_if_truncated(model, getattr(final, "stop_reason", None), task=task_id)
    text_blocks = [b for b in final.content if getattr(b, "type", None) == "text"]
    if not text_blocks:
        raise RuntimeError("Claude returned no text block in streaming response")
    final_text = text_blocks[-1].text

    usage = final.usage
    usage_dict = {
        "input_tokens": getattr(usage, "input_tokens", 0),
        "output_tokens": getattr(usage, "output_tokens", 0),
        "cache_read": getattr(usage, "cache_read_input_tokens", 0),
        "cache_creation": getattr(usage, "cache_creation_input_tokens", 0),
    }
    return final_text, usage_dict


def stream_message(
    client: Any,
    task_id: str,
    *,
    model: str,
    max_tokens: int,
    system: Any,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> tuple[Any, dict[str, int]]:
    """Run one streamed Anthropic call and return the FULL final message + usage.

    Unlike ``stream_and_emit`` (which returns only the last text block), this
    returns the raw ``Message`` so the caller can inspect ``stop_reason`` and any
    ``tool_use`` blocks to drive a client-side agentic loop (Issue 152). Token
    ``text_delta``s are still forwarded as ``token`` SSE events; ``tool_use``
    input deltas are not human-readable text and are dropped by ``_forward_event``.

    The caller emits the terminal ``done`` / ``error`` event — this only forwards
    intra-stream events for a single LLM round-trip.
    """
    stream_kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
    }
    if tools is not None:
        stream_kwargs["tools"] = tools

    with client.messages.stream(**stream_kwargs) as stream:
        for event in stream:
            try:
                _forward_event(task_id, event)
            except Exception as exc:
                logger.warning("stream_message: forward failed task=%s err=%s", task_id, exc)
        final = stream.get_final_message()

    warn_if_truncated(model, getattr(final, "stop_reason", None), task=task_id)
    usage = final.usage
    usage_dict = {
        "input_tokens": getattr(usage, "input_tokens", 0),
        "output_tokens": getattr(usage, "output_tokens", 0),
        "cache_read": getattr(usage, "cache_read_input_tokens", 0),
        "cache_creation": getattr(usage, "cache_creation_input_tokens", 0),
    }
    return final, usage_dict


def _forward_event(task_id: str, event: Any) -> None:
    """Forward a single SDK event to the progress stream.

    Split out so the try/except in the main loop wraps exactly the right scope.
    """
    etype = getattr(event, "type", None)

    if etype == "message_start":
        # Cache stats live on message_start.message.usage — readable BEFORE
        # the first token, so the UI can show "cache HIT" instantly.
        msg = getattr(event, "message", None)
        usage = getattr(msg, "usage", None) if msg is not None else None
        if usage is not None:
            sync_emit(
                task_id,
                "cache",
                input_tokens=getattr(usage, "input_tokens", 0),
                cache_read=getattr(usage, "cache_read_input_tokens", 0),
                cache_creation=getattr(usage, "cache_creation_input_tokens", 0),
            )
        return

    if etype == "content_block_delta":
        delta = getattr(event, "delta", None)
        if delta is None:
            return
        dtype = getattr(delta, "type", "")
        if dtype == "text_delta":
            sync_emit(task_id, "token", chunk=getattr(delta, "text", ""))
        elif dtype == "thinking_delta":
            # Forwarded but won't fire on anthropic==0.40 (SDK predates
            # extended thinking) — wakes up automatically once Issue 84
            # bumps the SDK + enables thinking on the call.
            sync_emit(task_id, "thinking", chunk=getattr(delta, "thinking", ""))
        # Unknown delta types (signature_delta, input_json_delta, future
        # types) are silently dropped — they carry no human-readable text.
