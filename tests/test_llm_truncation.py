"""Issue 331 — detect LLM responses cut off at max_tokens.

``stop_reason == "max_tokens"`` means Claude hit the output cap mid-answer. JSON
producers then fail to parse and fall back silently, so a truncation looks identical
to an empty response in the logs. ``warn_if_truncated`` surfaces it; the streaming
wrapper calls it for every streaming caller, and the non-streaming ``.create()`` sites
call it directly. These tests lock the helper + the wrapper wiring.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

from observability import warn_if_truncated
from worker import anthropic_stream
from worker.anthropic_stream import stream_and_emit

# Reuse the fake-stream scaffolding shape from test_anthropic_stream.py.


class _FakeStream:
    def __init__(self, events, final_message):
        self._events = events
        self._final = final_message

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        self._it = iter(self._events)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration from None

    async def get_final_message(self):
        return self._final


def _final(text="hi", *, stop_reason="end_turn"):
    block = SimpleNamespace(type="text", text=text)
    usage = SimpleNamespace(
        input_tokens=10,
        output_tokens=5,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )
    return SimpleNamespace(content=[block], usage=usage, stop_reason=stop_reason)


# ── warn_if_truncated helper ─────────────────────────────────────────────────


def test_warn_if_truncated_fires_on_max_tokens(caplog):
    with caplog.at_level(logging.WARNING, logger="observability"):
        result = warn_if_truncated("claude-sonnet-4-6", "max_tokens", task="t1")
    assert result is True
    assert any("truncated at max_tokens" in r.message for r in caplog.records)


def test_warn_if_truncated_silent_on_normal_stop(caplog):
    with caplog.at_level(logging.WARNING, logger="observability"):
        assert warn_if_truncated("m", "end_turn") is False
        assert warn_if_truncated("m", "tool_use") is False
        assert warn_if_truncated("m", None) is False
    assert not any("truncated" in r.message for r in caplog.records)


# ── stream_and_emit wires the check for every streaming caller ────────────────


async def test_stream_and_emit_warns_when_truncated(monkeypatch, caplog):
    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(anthropic_stream, "aemit", _noop)
    stream = _FakeStream([], _final("partial", stop_reason="max_tokens"))
    client = MagicMock()
    client.messages.stream.return_value = stream

    with caplog.at_level(logging.WARNING, logger="observability"):
        text, usage = await stream_and_emit(
            client, "task-1", model="claude-sonnet-4-6", max_tokens=10, system=[], messages=[]
        )
    assert text == "partial"  # still returns what it got
    assert any("truncated at max_tokens" in r.message for r in caplog.records)


async def test_stream_and_emit_quiet_when_complete(monkeypatch, caplog):
    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(anthropic_stream, "aemit", _noop)
    stream = _FakeStream([], _final("done", stop_reason="end_turn"))
    client = MagicMock()
    client.messages.stream.return_value = stream

    with caplog.at_level(logging.WARNING, logger="observability"):
        await stream_and_emit(client, "task-2", model="m", max_tokens=10, system=[], messages=[])
    assert not any("truncated" in r.message for r in caplog.records)
