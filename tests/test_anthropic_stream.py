"""Unit tests for worker.anthropic_stream.stream_and_emit (Issue 86).

The Anthropic SDK client is mocked at the boundary of `messages.stream()` —
we don't make any real LLM calls, just verify the wrapper iterates events,
forwards the right deltas through sync_emit, and returns final_text + usage.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from worker import anthropic_stream


def _fake_event(event_type: str, **kwargs) -> SimpleNamespace:
    """Build a SimpleNamespace mimicking an Anthropic SDK event object."""
    return SimpleNamespace(type=event_type, **kwargs)


class _FakeStream:
    """Mimics the context-manager + iterator returned by client.messages.stream()."""

    def __init__(self, events: list[SimpleNamespace], final_message: SimpleNamespace) -> None:
        self._events = events
        self._final = final_message

    def __enter__(self) -> _FakeStream:
        return self

    def __exit__(self, *exc) -> None:
        return None

    def __iter__(self):
        return iter(self._events)

    def get_final_message(self) -> SimpleNamespace:
        return self._final


def _build_final_message(text: str, *, in_tok=100, out_tok=50, cr=0, cc=0) -> SimpleNamespace:
    text_block = SimpleNamespace(type="text", text=text)
    usage = SimpleNamespace(
        input_tokens=in_tok,
        output_tokens=out_tok,
        cache_read_input_tokens=cr,
        cache_creation_input_tokens=cc,
    )
    return SimpleNamespace(content=[text_block], usage=usage, stop_reason="end_turn")


def test_forwards_message_start_usage_as_cache_event(monkeypatch) -> None:
    emitted: list[tuple] = []
    monkeypatch.setattr(
        anthropic_stream,
        "sync_emit",
        lambda task_id, etype, **fields: emitted.append((task_id, etype, fields)),
    )

    message_start = _fake_event(
        "message_start",
        message=SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=4200,
                cache_read_input_tokens=4000,
                cache_creation_input_tokens=0,
            )
        ),
    )
    final = _build_final_message("brief here", in_tok=4200, out_tok=120, cr=4000)
    stream = _FakeStream([message_start], final)

    client = MagicMock()
    client.messages.stream.return_value = stream

    text, usage = anthropic_stream.stream_and_emit(
        client, "task-1", model="m", max_tokens=2000, system=[], messages=[]
    )

    assert text == "brief here"
    assert usage["cache_read"] == 4000
    cache_emits = [e for e in emitted if e[1] == "cache"]
    assert len(cache_emits) == 1
    assert cache_emits[0][2] == {
        "input_tokens": 4200,
        "cache_read": 4000,
        "cache_creation": 0,
    }


def test_forwards_text_delta_as_token_events(monkeypatch) -> None:
    emitted: list[tuple] = []
    monkeypatch.setattr(
        anthropic_stream,
        "sync_emit",
        lambda task_id, etype, **fields: emitted.append((task_id, etype, fields)),
    )

    events = [
        _fake_event("content_block_delta", delta=SimpleNamespace(type="text_delta", text="Hello")),
        _fake_event("content_block_delta", delta=SimpleNamespace(type="text_delta", text=" world")),
    ]
    final = _build_final_message("Hello world")
    stream = _FakeStream(events, final)
    client = MagicMock()
    client.messages.stream.return_value = stream

    text, _ = anthropic_stream.stream_and_emit(
        client, "task-2", model="m", max_tokens=2000, system=[], messages=[]
    )

    assert text == "Hello world"
    token_emits = [e for e in emitted if e[1] == "token"]
    assert [e[2]["chunk"] for e in token_emits] == ["Hello", " world"]


def test_forwards_thinking_delta_as_thinking_events(monkeypatch) -> None:
    """Future-proofing: when the SDK is bumped in Issue 84 and extended
    thinking is enabled, thinking_delta should flow through unchanged."""
    emitted: list[tuple] = []
    monkeypatch.setattr(
        anthropic_stream,
        "sync_emit",
        lambda task_id, etype, **fields: emitted.append((task_id, etype, fields)),
    )

    events = [
        _fake_event(
            "content_block_delta",
            delta=SimpleNamespace(type="thinking_delta", thinking="Let me reason..."),
        ),
        _fake_event(
            "content_block_delta",
            delta=SimpleNamespace(type="text_delta", text="Answer"),
        ),
    ]
    final = _build_final_message("Answer")
    stream = _FakeStream(events, final)
    client = MagicMock()
    client.messages.stream.return_value = stream

    anthropic_stream.stream_and_emit(
        client, "task-3", model="m", max_tokens=2000, system=[], messages=[]
    )

    thinking_emits = [e for e in emitted if e[1] == "thinking"]
    assert len(thinking_emits) == 1
    assert thinking_emits[0][2]["chunk"] == "Let me reason..."


def test_ignores_unknown_delta_types_silently(monkeypatch) -> None:
    """signature_delta, input_json_delta, etc. should not crash the wrapper."""
    emitted: list[tuple] = []
    monkeypatch.setattr(
        anthropic_stream,
        "sync_emit",
        lambda task_id, etype, **fields: emitted.append((task_id, etype, fields)),
    )

    events = [
        _fake_event(
            "content_block_delta",
            delta=SimpleNamespace(type="signature_delta", signature="sig"),
        ),
        _fake_event(
            "content_block_delta",
            delta=SimpleNamespace(type="input_json_delta", partial_json="{"),
        ),
        _fake_event("content_block_delta", delta=SimpleNamespace(type="text_delta", text="hi")),
    ]
    final = _build_final_message("hi")
    stream = _FakeStream(events, final)
    client = MagicMock()
    client.messages.stream.return_value = stream

    text, _ = anthropic_stream.stream_and_emit(
        client, "task-4", model="m", max_tokens=2000, system=[], messages=[]
    )
    assert text == "hi"
    # Only the text_delta becomes a token event; the others are silently dropped
    types_seen = [e[1] for e in emitted]
    assert types_seen == ["token"]


def test_returns_final_usage_dict_shape(monkeypatch) -> None:
    monkeypatch.setattr(anthropic_stream, "sync_emit", lambda *a, **k: None)

    final = _build_final_message("result", in_tok=4000, out_tok=890, cr=3800, cc=200)
    stream = _FakeStream([], final)
    client = MagicMock()
    client.messages.stream.return_value = stream

    _, usage = anthropic_stream.stream_and_emit(
        client, "task-5", model="m", max_tokens=2000, system=[], messages=[]
    )
    assert usage == {
        "input_tokens": 4000,
        "output_tokens": 890,
        "cache_read": 3800,
        "cache_creation": 200,
    }


def test_returns_last_text_block_when_multiple_present(monkeypatch) -> None:
    """Same defensive pattern as dna/brief.py — last text block wins; non-text
    blocks (thinking, tool_use) are filtered out."""
    monkeypatch.setattr(anthropic_stream, "sync_emit", lambda *a, **k: None)

    blocks = [
        SimpleNamespace(type="thinking", thinking="..."),
        SimpleNamespace(type="text", text="first"),
        SimpleNamespace(type="text", text="last"),
    ]
    usage = SimpleNamespace(
        input_tokens=0,
        output_tokens=0,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )
    final = SimpleNamespace(content=blocks, usage=usage, stop_reason="end_turn")
    stream = _FakeStream([], final)
    client = MagicMock()
    client.messages.stream.return_value = stream

    text, _ = anthropic_stream.stream_and_emit(
        client, "task-6", model="m", max_tokens=2000, system=[], messages=[]
    )
    assert text == "last"


def test_raises_on_no_text_blocks(monkeypatch) -> None:
    monkeypatch.setattr(anthropic_stream, "sync_emit", lambda *a, **k: None)

    blocks = [SimpleNamespace(type="thinking", thinking="...")]
    usage = SimpleNamespace(
        input_tokens=0,
        output_tokens=0,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )
    final = SimpleNamespace(content=blocks, usage=usage, stop_reason="end_turn")
    stream = _FakeStream([], final)
    client = MagicMock()
    client.messages.stream.return_value = stream

    with pytest.raises(RuntimeError, match="no text block"):
        anthropic_stream.stream_and_emit(
            client, "task-7", model="m", max_tokens=2000, system=[], messages=[]
        )


def test_emit_failures_inside_loop_do_not_abort_iteration(monkeypatch) -> None:
    """A Redis hiccup mid-stream must not lose us the final text."""
    call_count = {"n": 0}

    def flaky_emit(task_id, etype, **fields):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("redis down")

    monkeypatch.setattr(anthropic_stream, "sync_emit", flaky_emit)

    events = [
        _fake_event("content_block_delta", delta=SimpleNamespace(type="text_delta", text="a")),
        _fake_event("content_block_delta", delta=SimpleNamespace(type="text_delta", text="b")),
        _fake_event("content_block_delta", delta=SimpleNamespace(type="text_delta", text="c")),
    ]
    final = _build_final_message("abc")
    stream = _FakeStream(events, final)
    client = MagicMock()
    client.messages.stream.return_value = stream

    # The wrapper must still complete and return the final text even though
    # one of the sync_emit calls blew up.
    text, _ = anthropic_stream.stream_and_emit(
        client, "task-8", model="m", max_tokens=2000, system=[], messages=[]
    )
    assert text == "abc"


# ── Wave-3 Fix A: tools kwarg flows through to client.messages.stream ────────


def test_tools_kwarg_forwarded_to_stream_when_provided() -> None:
    """Wave-3 Fix A: ``stream_and_emit`` must forward ``tools`` to
    ``client.messages.stream(...)`` when the caller supplies them. Without
    this, callers like ``improvement/brief.py`` that depend on ``web_search``
    silently lose grounding — the SEV1 closed by Wave 3.
    """
    final = _build_final_message("brief")
    stream = _FakeStream([], final)
    client = MagicMock()
    client.messages.stream.return_value = stream

    tools = [{"type": "web_search_20260209", "name": "web_search"}]
    anthropic_stream.stream_and_emit(
        client,
        "task-tools",
        model="m",
        max_tokens=2000,
        system=[],
        messages=[],
        tools=tools,
    )

    # The SDK call must have received the tools argument verbatim.
    assert client.messages.stream.call_count == 1
    call_kwargs = client.messages.stream.call_args.kwargs
    assert "tools" in call_kwargs, (
        "stream_and_emit must forward `tools` to client.messages.stream(...) "
        "— Wave-3 Fix A closes the SEV1 where improvement brief streamed "
        "without web_search grounding."
    )
    assert call_kwargs["tools"] == tools


def test_tools_kwarg_dropped_when_none() -> None:
    """When `tools` is not provided (default None), the SDK kwarg must be
    absent — passing `tools=None` to older SDK shapes can raise.
    ``dna/brief.py``'s call path (no tools) must keep working unchanged."""
    final = _build_final_message("brief")
    stream = _FakeStream([], final)
    client = MagicMock()
    client.messages.stream.return_value = stream

    anthropic_stream.stream_and_emit(
        client,
        "task-no-tools",
        model="m",
        max_tokens=2000,
        system=[],
        messages=[],
    )

    call_kwargs = client.messages.stream.call_args.kwargs
    assert "tools" not in call_kwargs, (
        "No-tools callers must produce a call without the tools kwarg "
        "(older SDKs reject tools=None) — pinning the dna/brief.py shape."
    )
