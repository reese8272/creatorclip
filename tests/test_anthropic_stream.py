"""Unit tests for worker.anthropic_stream.stream_and_emit (Issue 86; async since 82a).

The Anthropic SDK client is mocked at the boundary of `messages.stream()` —
we don't make any real LLM calls, just verify the async wrapper iterates
events, forwards the right deltas through aemit, and returns final_text +
usage. Assertions are ported unchanged from the sync-era suite: token order,
the cache event from message_start, usage propagation, and emit-failure
tolerance are the contract.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from worker import anthropic_stream


def _fake_event(event_type: str, **kwargs) -> SimpleNamespace:
    """Build a SimpleNamespace mimicking an Anthropic SDK event object."""
    return SimpleNamespace(type=event_type, **kwargs)


class _FakeAsyncStream:
    """Mimics the async context-manager + async iterator returned by
    AsyncAnthropic's client.messages.stream()."""

    def __init__(self, events: list[SimpleNamespace], final_message: SimpleNamespace) -> None:
        self._events = events
        self._final = final_message

    async def __aenter__(self) -> _FakeAsyncStream:
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    def __aiter__(self):
        self._it = iter(self._events)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration from None

    async def get_final_message(self) -> SimpleNamespace:
        return self._final


def _build_final_message(
    text: str, *, in_tok=100, out_tok=50, cr=0, cc=0, stop_reason="end_turn"
) -> SimpleNamespace:
    text_block = SimpleNamespace(type="text", text=text)
    usage = SimpleNamespace(
        input_tokens=in_tok,
        output_tokens=out_tok,
        cache_read_input_tokens=cr,
        cache_creation_input_tokens=cc,
    )
    return SimpleNamespace(content=[text_block], usage=usage, stop_reason=stop_reason)


def _capture_aemit(monkeypatch, emitted: list[tuple]) -> None:
    async def _aemit(task_id, etype, **fields):
        emitted.append((task_id, etype, fields))

    monkeypatch.setattr(anthropic_stream, "aemit", _aemit)


def _client_returning(stream: _FakeAsyncStream) -> MagicMock:
    client = MagicMock()
    client.messages.stream.return_value = stream
    return client


async def test_forwards_message_start_usage_as_cache_event(monkeypatch) -> None:
    emitted: list[tuple] = []
    _capture_aemit(monkeypatch, emitted)

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
    client = _client_returning(_FakeAsyncStream([message_start], final))

    text, usage = await anthropic_stream.stream_and_emit(
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


async def test_cache_event_emitted_before_first_token(monkeypatch) -> None:
    """The `cache` event (from message_start.usage) must precede the first
    `token` event in emit order — the UI shows cache HIT before text streams."""
    emitted: list[tuple] = []
    _capture_aemit(monkeypatch, emitted)

    events = [
        _fake_event(
            "message_start",
            message=SimpleNamespace(
                usage=SimpleNamespace(
                    input_tokens=10,
                    cache_read_input_tokens=0,
                    cache_creation_input_tokens=0,
                )
            ),
        ),
        _fake_event("content_block_delta", delta=SimpleNamespace(type="text_delta", text="Hi")),
    ]
    final = _build_final_message("Hi")
    client = _client_returning(_FakeAsyncStream(events, final))

    await anthropic_stream.stream_and_emit(
        client, "task-order", model="m", max_tokens=2000, system=[], messages=[]
    )

    types_seen = [e[1] for e in emitted]
    assert types_seen == ["cache", "token"]


async def test_forwards_text_delta_as_token_events(monkeypatch) -> None:
    emitted: list[tuple] = []
    _capture_aemit(monkeypatch, emitted)

    events = [
        _fake_event("content_block_delta", delta=SimpleNamespace(type="text_delta", text="Hello")),
        _fake_event("content_block_delta", delta=SimpleNamespace(type="text_delta", text=" world")),
    ]
    final = _build_final_message("Hello world")
    client = _client_returning(_FakeAsyncStream(events, final))

    text, _ = await anthropic_stream.stream_and_emit(
        client, "task-2", model="m", max_tokens=2000, system=[], messages=[]
    )

    assert text == "Hello world"
    token_emits = [e for e in emitted if e[1] == "token"]
    assert [e[2]["chunk"] for e in token_emits] == ["Hello", " world"]


async def test_forwards_thinking_delta_as_thinking_events(monkeypatch) -> None:
    """When extended thinking is enabled, thinking_delta flows through unchanged."""
    emitted: list[tuple] = []
    _capture_aemit(monkeypatch, emitted)

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
    client = _client_returning(_FakeAsyncStream(events, final))

    await anthropic_stream.stream_and_emit(
        client, "task-3", model="m", max_tokens=2000, system=[], messages=[]
    )

    thinking_emits = [e for e in emitted if e[1] == "thinking"]
    assert len(thinking_emits) == 1
    assert thinking_emits[0][2]["chunk"] == "Let me reason..."


async def test_ignores_unknown_delta_types_silently(monkeypatch) -> None:
    """signature_delta, input_json_delta, etc. should not crash the wrapper."""
    emitted: list[tuple] = []
    _capture_aemit(monkeypatch, emitted)

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
    client = _client_returning(_FakeAsyncStream(events, final))

    text, _ = await anthropic_stream.stream_and_emit(
        client, "task-4", model="m", max_tokens=2000, system=[], messages=[]
    )
    assert text == "hi"
    # Only the text_delta becomes a token event; the others are silently dropped
    types_seen = [e[1] for e in emitted]
    assert types_seen == ["token"]


async def test_returns_final_usage_dict_shape(monkeypatch) -> None:
    _capture_aemit(monkeypatch, [])

    final = _build_final_message("result", in_tok=4000, out_tok=890, cr=3800, cc=200)
    client = _client_returning(_FakeAsyncStream([], final))

    _, usage = await anthropic_stream.stream_and_emit(
        client, "task-5", model="m", max_tokens=2000, system=[], messages=[]
    )
    assert usage == {
        "input_tokens": 4000,
        "output_tokens": 890,
        "cache_read": 3800,
        "cache_creation": 200,
    }


async def test_returns_last_text_block_when_multiple_present(monkeypatch) -> None:
    """Same defensive pattern as dna/brief.py — last text block wins; non-text
    blocks (thinking, tool_use) are filtered out."""
    _capture_aemit(monkeypatch, [])

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
    client = _client_returning(_FakeAsyncStream([], final))

    text, _ = await anthropic_stream.stream_and_emit(
        client, "task-6", model="m", max_tokens=2000, system=[], messages=[]
    )
    assert text == "last"


async def test_raises_on_no_text_blocks(monkeypatch) -> None:
    _capture_aemit(monkeypatch, [])

    blocks = [SimpleNamespace(type="thinking", thinking="...")]
    usage = SimpleNamespace(
        input_tokens=0,
        output_tokens=0,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )
    final = SimpleNamespace(content=blocks, usage=usage, stop_reason="end_turn")
    client = _client_returning(_FakeAsyncStream([], final))

    with pytest.raises(RuntimeError, match="no text block"):
        await anthropic_stream.stream_and_emit(
            client, "task-7", model="m", max_tokens=2000, system=[], messages=[]
        )


async def test_emit_failures_inside_loop_do_not_abort_iteration(monkeypatch) -> None:
    """A Redis hiccup mid-stream must not lose us the final text."""
    call_count = {"n": 0}

    async def flaky_emit(task_id, etype, **fields):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("redis down")

    monkeypatch.setattr(anthropic_stream, "aemit", flaky_emit)

    events = [
        _fake_event("content_block_delta", delta=SimpleNamespace(type="text_delta", text="a")),
        _fake_event("content_block_delta", delta=SimpleNamespace(type="text_delta", text="b")),
        _fake_event("content_block_delta", delta=SimpleNamespace(type="text_delta", text="c")),
    ]
    final = _build_final_message("abc")
    client = _client_returning(_FakeAsyncStream(events, final))

    # The wrapper must still complete and return the final text even though
    # one of the aemit calls blew up.
    text, _ = await anthropic_stream.stream_and_emit(
        client, "task-8", model="m", max_tokens=2000, system=[], messages=[]
    )
    assert text == "abc"


# ── stream_message: full-message shape + stop_reason propagation ─────────────


async def test_stream_message_returns_full_message_and_stop_reason(monkeypatch) -> None:
    """stream_message must return the raw Message (stop_reason + content intact)
    so agentic callers (chat/runner.py pause_turn / tool_use handling) can
    inspect it — the Issue-152 contract, preserved across the async rewrite."""
    _capture_aemit(monkeypatch, [])

    final = _build_final_message("answer", in_tok=10, out_tok=5, stop_reason="pause_turn")
    client = _client_returning(_FakeAsyncStream([], final))

    message, usage = await anthropic_stream.stream_message(
        client, "task-sm", model="m", max_tokens=2000, system=[], messages=[]
    )
    assert message is final
    assert message.stop_reason == "pause_turn"
    assert usage == {
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read": 0,
        "cache_creation": 0,
    }


# ── Wave-3 Fix A: tools kwarg flows through to client.messages.stream ────────


async def test_tools_kwarg_forwarded_to_stream_when_provided(monkeypatch) -> None:
    """Wave-3 Fix A: ``stream_and_emit`` must forward ``tools`` to
    ``client.messages.stream(...)`` when the caller supplies them. Without
    this, callers like ``improvement/brief.py`` that depend on ``web_search``
    silently lose grounding — the SEV1 closed by Wave 3.
    """
    _capture_aemit(monkeypatch, [])

    final = _build_final_message("brief")
    client = _client_returning(_FakeAsyncStream([], final))

    tools = [{"type": "web_search_20260209", "name": "web_search"}]
    await anthropic_stream.stream_and_emit(
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


async def test_tools_kwarg_dropped_when_none(monkeypatch) -> None:
    """When `tools` is not provided (default None), the SDK kwarg must be
    absent — passing `tools=None` to older SDK shapes can raise.
    ``dna/brief.py``'s call path (no tools) must keep working unchanged."""
    _capture_aemit(monkeypatch, [])

    final = _build_final_message("brief")
    client = _client_returning(_FakeAsyncStream([], final))

    await anthropic_stream.stream_and_emit(
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


# ── stream_until_final: shared pause_turn continuation loop (Issue 361) ──────


async def test_stream_until_final_continues_on_pause_turn_and_sums_usage(monkeypatch) -> None:
    """pause_turn re-calls stream_message with the assistant turn appended
    (same tools), returns the final message, and sums usage across rounds."""
    calls: list[dict] = []
    responses = [
        (
            _build_final_message(
                "searching…", in_tok=10, out_tok=5, cr=1, cc=2, stop_reason="pause_turn"
            ),
            {"input_tokens": 10, "output_tokens": 5, "cache_read": 1, "cache_creation": 2},
        ),
        (
            _build_final_message("answer", in_tok=20, out_tok=30, cr=3, cc=4),
            {"input_tokens": 20, "output_tokens": 30, "cache_read": 3, "cache_creation": 4},
        ),
    ]

    async def _fake_stream_message(client, task_id, **kwargs):
        calls.append(kwargs)
        return responses[len(calls) - 1]

    monkeypatch.setattr(anthropic_stream, "stream_message", _fake_stream_message)

    tools = [{"type": "web_search_20260209", "name": "web_search"}]
    msg, usage = await anthropic_stream.stream_until_final(
        MagicMock(),
        "task-suf",
        model="m",
        max_tokens=2000,
        system=[],
        messages=[{"role": "user", "content": "go"}],
        tools=tools,
    )

    assert len(calls) == 2
    assert calls[0]["tools"] is tools and calls[1]["tools"] is tools
    assert calls[1]["messages"][-1]["role"] == "assistant"
    assert msg is responses[1][0]
    assert usage == {"input_tokens": 30, "output_tokens": 35, "cache_read": 4, "cache_creation": 6}


async def test_stream_until_final_bounds_rounds_and_warns(monkeypatch, caplog) -> None:
    """A model that never leaves pause_turn stops after max_rounds resumes and
    logs the caller-supplied round-cap warning."""
    calls = {"n": 0}
    paused = _build_final_message("still going…", in_tok=1, out_tok=1, stop_reason="pause_turn")

    async def _always_pause(client, task_id, **kwargs):
        calls["n"] += 1
        return paused, {"input_tokens": 1, "output_tokens": 1, "cache_read": 0, "cache_creation": 0}

    monkeypatch.setattr(anthropic_stream, "stream_message", _always_pause)

    with caplog.at_level("WARNING", logger="worker.anthropic_stream"):
        msg, usage = await anthropic_stream.stream_until_final(
            MagicMock(),
            "task-suf-cap",
            model="m",
            max_tokens=2000,
            system=[],
            messages=[{"role": "user", "content": "go"}],
            max_rounds=2,
            round_cap_warning="caller_x: hit max web_search rounds (%d)",
        )

    assert calls["n"] == 3  # initial call + max_rounds resumes
    assert msg is paused
    assert usage["input_tokens"] == 3
    assert "caller_x: hit max web_search rounds (2)" in caplog.text
