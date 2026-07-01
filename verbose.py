"""Verbose full-content logging sink for pre-production debugging.

Every helper here is a NO-OP unless ``settings.verbose_logging_enabled`` is True.
Off-prod that means ``VERBOSE_LOGGING=true``; in production it additionally requires
the explicit ``VERBOSE_LOGGING_ALLOW_PROD=true`` opt-in (the private-beta case — see
config). When active, each load-bearing operation writes a COMPLETE record (raw
prompt/response/transcript content, full request bodies, full ffmpeg commands, full
tracebacks) to the dedicated ``verbose`` logger.

That logger writes ``<LOG_DIR>/verbose-*.log`` (or stdout when ``LOG_DIR=""``) through
a NON-scrubbing JSON formatter (see ``observability.configure_logging``) so content
survives verbatim — this is a deliberate, documented deviation from the PII/secret
redaction boundary (docs/COMPLIANCE.md), recorded in docs/DECISIONS.md (2026-06-29).
The whole gate lives in ``settings.verbose_logging_enabled``; this module trusts that
single check so callers never have to.

The logger has ``propagate=False`` and its own handlers, so verbose output never
bleeds into ``app.log`` / ``worker.log`` or the existing root-logger assertions.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from config import settings

VERBOSE_LOGGER_NAME = "verbose"
_log = logging.getLogger(VERBOSE_LOGGER_NAME)


def verbose_enabled() -> bool:
    """Single source of truth for whether verbose full-content logging is active."""
    return settings.verbose_logging_enabled


def vlog(event: str, **fields: Any) -> None:
    """Write one full-content verbose record. No-op unless verbose logging is on.

    ``event`` is a short snake_case name (``llm_request``, ``task_retry``, …);
    ``fields`` land as top-level keys in the verbose JSON line. Values are emitted
    verbatim — callers may pass raw prompts, responses, and tracebacks.
    """
    if not settings.verbose_logging_enabled:
        return
    _log.info(event, extra={"event": event, **fields})


def _usage_dict(usage: Any) -> dict[str, Any] | None:
    """Normalize an Anthropic ``Usage`` object or a stream usage dict to a plain dict."""
    if usage is None:
        return None
    if isinstance(usage, dict):
        return usage
    return {
        "input_tokens": getattr(usage, "input_tokens", 0),
        "output_tokens": getattr(usage, "output_tokens", 0),
        "cache_read": getattr(usage, "cache_read_input_tokens", 0),
        "cache_creation": getattr(usage, "cache_creation_input_tokens", 0),
    }


def _message_text(message: Any) -> str | None:
    """Join the text blocks of an Anthropic ``Message`` for readable logging."""
    content = getattr(message, "content", None)
    if not content:
        return None
    parts = [getattr(b, "text", "") for b in content if getattr(b, "type", None) == "text"]
    joined = "\n".join(p for p in parts if p)
    return joined or None


def vlog_llm_request(
    feature: str,
    *,
    model: Any,
    max_tokens: Any = None,
    system: Any = None,
    messages: Any = None,
    tools: Any = None,
    streaming: bool = False,
    **extra: Any,
) -> None:
    """Record a full LLM request — system prompt, messages, tools — verbatim."""
    if not settings.verbose_logging_enabled:
        return
    vlog(
        "llm_request",
        feature=feature,
        model=model,
        max_tokens=max_tokens,
        streaming=streaming,
        system=system,
        messages=messages,
        tools=tools,
        **extra,
    )


def vlog_llm_response(
    feature: str,
    *,
    response: Any = None,
    text: Any = None,
    usage: Any = None,
    model: Any = None,
    stop_reason: Any = None,
    duration_ms: Any = None,
    error: Any = None,
    **extra: Any,
) -> None:
    """Record a full LLM response. Pass either a raw ``response`` (Anthropic
    ``Message``) — fields are extracted — or the individual ``text``/``usage``/…
    pieces (used by the streaming wrappers, which return a usage dict)."""
    if not settings.verbose_logging_enabled:
        return
    if response is not None:
        model = model if model is not None else getattr(response, "model", None)
        usage = usage if usage is not None else getattr(response, "usage", None)
        if stop_reason is None:
            stop_reason = getattr(response, "stop_reason", None)
        if text is None:
            text = _message_text(response)
    vlog(
        "llm_response",
        feature=feature,
        model=model,
        text=text,
        usage=_usage_dict(usage),
        stop_reason=stop_reason,
        duration_ms=duration_ms,
        error=error,
        **extra,
    )


def now_ms() -> float:
    """Monotonic millisecond clock for duration measurement (verbose timings)."""
    return time.perf_counter() * 1000.0
