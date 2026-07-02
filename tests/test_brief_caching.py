"""
Unit tests for Issue 69 — prompt-cache split + web_search text extraction.

DB-free: the Anthropic client is patched; we assert on the request `system` shape
(static cached block vs volatile uncached block) and on which text block is
returned. No real API call, no DB.
"""

import pytest


class _Block:
    def __init__(self, type_: str, text: str | None = None):
        self.type = type_
        self.text = text


class _Usage:
    input_tokens = 10
    output_tokens = 5
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0


class _Resp:
    def __init__(self, content: list):
        self.content = content
        self.usage = _Usage()


def test_dna_brief_splits_static_prefix_from_volatile_data(mocker):
    """Issue 315: dna/brief.py has NO cache_control markers on any block.
    The static instructions block is ~570–650 tokens — below Sonnet 4.6's
    1024-token cacheable floor. An inert marker charges the write-premium
    with zero cache reads; it was removed in Issue 315.
    Block 0: stable global instructions (no cache_control).
    Block 1: volatile per-creator performance corpus (no cache_control).
    See docs/DECISIONS.md (Issues 223/224/315)."""
    import dna.brief as b

    captured: dict = {}

    def _create(**kwargs):
        captured.update(kwargs)
        return _Resp([_Block("text", "BRIEF BODY")])

    mocker.patch.object(b._ANTHROPIC.messages, "create", side_effect=_create)

    out, _usage = b.generate_brief(
        {"top_videos": [{"title": "T", "hook_text": "H"}]}, "Acme Channel"
    )

    system = captured["system"]
    assert len(system) == 2
    # Block 0: static instructions — no cache_control (prefix below 1024-token floor).
    assert "cache_control" not in system[0], (
        "Issue 315: dna/brief.py static block must have no cache_control — "
        "~570–650 tokens is below Sonnet 4.6's 1024-token cacheable floor."
    )
    assert "Acme Channel" not in system[0]["text"]
    assert "CREATOR PERFORMANCE DATA" not in system[0]["text"]
    # Block 1: volatile performance corpus — never cached.
    assert "cache_control" not in system[1]
    assert "CREATOR PERFORMANCE DATA" in system[1]["text"]
    assert out.startswith("BRIEF BODY")


def test_dna_brief_stated_identity_in_user_turn_not_system(mocker):
    """Issue 224: stated_identity must appear in the user message content, not in
    any system block. The model must receive it from the user role so it is treated
    as untrusted user content, not as trusted operator instructions."""
    import dna.brief as b

    captured: dict = {}

    def _create(**kwargs):
        captured.update(kwargs)
        return _Resp([_Block("text", "BRIEF BODY")])

    mocker.patch.object(b._ANTHROPIC.messages, "create", side_effect=_create)

    identity = "I make educational Python tutorials for beginners."
    b.generate_brief(
        {"top_videos": [{"title": "T", "hook_text": "H"}]},
        "Acme Channel",
        stated_identity=identity,
    )

    system = captured["system"]
    messages = captured["messages"]

    # stated_identity must NOT appear in any system block.
    for block in system:
        assert identity not in block["text"], (
            "Issue 224: stated_identity must not appear in any system block — "
            "system blocks are treated as trusted operator instructions."
        )

    # stated_identity must appear JSON-encoded in the user turn.
    user_content = messages[0]["content"]
    assert "creator_stated_identity" in user_content, (
        "Issue 224: user turn must contain the wrap_untrusted label for stated_identity."
    )
    # The identity text must be JSON-encoded (not raw) in the user message.
    import json

    assert json.dumps(identity) in user_content, (
        "Issue 224: stated_identity must be JSON-encoded inside wrap_untrusted."
    )


def test_improvement_brief_returns_final_text_block_not_preamble(mocker):
    import improvement.brief as b

    captured: dict = {}

    def _create(**kwargs):
        captured.update(kwargs)
        # web_search interleaving: preamble → tool_use → final answer.
        return _Resp(
            [
                _Block("text", "Let me search for current guidance..."),
                _Block("server_tool_use"),
                _Block("text", "FINAL ANSWER"),
            ]
        )

    mock_client = mocker.MagicMock()
    mock_client.messages.create.side_effect = _create
    mocker.patch.object(b._ANTHROPIC, "with_options", return_value=mock_client)

    out, _usage = b.generate_improvement_brief(channel_title="Ch", analytics={"avg_views": 100})

    system = captured["system"]
    assert len(system) == 2
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    assert "CREATOR ANALYTICS DATA" not in system[0]["text"]
    assert "cache_control" not in system[1]
    # The final text block is returned, not the "Let me search..." preamble.
    assert out.startswith("FINAL ANSWER")
    assert "Let me search" not in out


def test_improvement_brief_raises_when_no_text(mocker):
    import improvement.brief as b

    mock_client = mocker.MagicMock()
    mock_client.messages.create.return_value = _Resp([_Block("server_tool_use")])
    mocker.patch.object(b._ANTHROPIC, "with_options", return_value=mock_client)

    with pytest.raises(RuntimeError, match="no text"):
        b.generate_improvement_brief(channel_title="Ch", analytics={})


# ── Issue 84 — web_search tool string is the current GA version ──────────────


def test_default_web_search_tool_is_current_ga_version():
    """Issue 84 Win A: ANTHROPIC_WEB_SEARCH_TOOL must default to the GA tool with
    dynamic filtering (web_search_20260209), not the legacy _20250305. Dynamic
    filtering lets Claude pre-filter search results in a sandboxed code-exec
    step before they reach the main context — measurable token-cost + latency
    win on the improvement brief. Tool API shape is unchanged; the bump is the
    config-default change."""
    from config import Settings

    s = Settings()
    assert s.ANTHROPIC_WEB_SEARCH_TOOL == "web_search_20260209", (
        "Issue 84 — default web_search tool must be _20260209 (GA with dynamic "
        "filtering). _20250305 is still functional but lacks dynamic filtering."
    )


def test_improvement_brief_request_uses_configured_web_search_tool(mocker):
    """The improvement brief request body must carry the configured tool string
    on the tools list. Pins the wiring from config → call site so a future
    config change is actually reflected in the Anthropic call."""
    import improvement.brief as b

    captured: dict = {}

    def _create(**kwargs):
        captured.update(kwargs)
        return _Resp([_Block("text", "FINAL")])

    mock_client = mocker.MagicMock()
    mock_client.messages.create.side_effect = _create
    mocker.patch.object(b._ANTHROPIC, "with_options", return_value=mock_client)

    b.generate_improvement_brief(channel_title="Ch", analytics={"avg_views": 100})

    tools = captured["tools"]
    assert len(tools) == 1
    assert tools[0]["name"] == "web_search"
    # Issue 84 Win A: the type must be the current GA tool version, not legacy.
    assert tools[0]["type"] == "web_search_20260209", (
        f"Improvement brief must use the GA web_search tool (_20260209 with "
        f"dynamic filtering); got {tools[0]['type']!r}. Check "
        f"ANTHROPIC_WEB_SEARCH_TOOL config wiring."
    )


# ── Wave-3 Fix A: streaming path also receives web_search tools ──────────────


def test_improvement_brief_streaming_path_passes_tools_to_stream_message(mocker):
    """SEV1 regression: streaming path must pass tools=[web_search] to stream_message.
    Issue 350 switched the streaming branch from stream_and_emit to stream_message
    (needed to inspect stop_reason for the pause_turn loop). This test pins the
    wiring: when task_id is set, stream_message receives tools and the final text
    block is returned with the honesty disclaimer appended.
    """
    import improvement.brief as b
    from config import settings

    captured: dict = {}

    class _FakeMsg:
        stop_reason = "end_turn"

        class _Block:
            type = "text"
            text = "FINAL ANSWER"

        content = [_Block()]

    def _fake_stream_message(client, task_id, **kwargs):
        captured.update(kwargs)
        captured["task_id"] = task_id
        return (
            _FakeMsg(),
            {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read": 0,
                "cache_creation": 0,
            },
        )

    mocker.patch("worker.anthropic_stream.stream_message", side_effect=_fake_stream_message)

    result, _usage = b.generate_improvement_brief(
        channel_title="Ch",
        analytics={"avg_views": 100},
        task_id="task-w3-fix-a",
    )

    assert captured["task_id"] == "task-w3-fix-a"
    assert "tools" in captured, (
        "streaming branch must pass tools=tools to stream_message — "
        "without this the improvement brief loses web_search grounding (SEV1)."
    )
    tools = captured["tools"]
    assert len(tools) == 1
    assert tools[0]["name"] == "web_search"
    assert tools[0]["type"] == settings.ANTHROPIC_WEB_SEARCH_TOOL
    assert "FINAL ANSWER" in result
    assert "does not promise virality" in result


# ── Issue 350 — pause_turn loop ───────────────────────────────────────────────


def test_improvement_brief_pause_turn_loop_continues_on_web_search(mocker):
    """When stop_reason == 'pause_turn' the loop re-calls create() with the
    assistant turn appended and uses the final synthesised answer, not the
    search-trigger preamble. max_uses=5 is set on the tool definition.
    """
    import improvement.brief as b

    class _PausedResp:
        stop_reason = "pause_turn"
        usage = type(
            "U",
            (),
            {
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        )()

        class _Block:
            type = "text"
            text = "let me search…"

        content = [_Block()]

    class _FinalResp:
        stop_reason = "end_turn"
        usage = type(
            "U",
            (),
            {
                "input_tokens": 20,
                "output_tokens": 30,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        )()

        class _Block:
            type = "text"
            text = "SYNTHESISED ANSWER"

        content = [_Block()]

    responses = [_PausedResp(), _FinalResp()]
    call_args: list = []

    def _create(**kwargs):
        call_args.append(kwargs)
        return responses[len(call_args) - 1]

    mock_client = mocker.MagicMock()
    mock_client.messages.create.side_effect = _create
    mocker.patch.object(b._ANTHROPIC, "with_options", return_value=mock_client)

    result, _usage = b.generate_improvement_brief(channel_title="Ch", analytics={})

    assert mock_client.messages.create.call_count == 2, (
        "pause_turn must trigger exactly one continuation call"
    )
    # Second call must have the assistant turn appended to messages
    second_messages = call_args[1]["messages"]
    assert len(second_messages) == 2  # original user + assistant with search results
    assert second_messages[1]["role"] == "assistant"
    # Final answer is the second response's text, not the search preamble
    assert "SYNTHESISED ANSWER" in result
    assert "let me search" not in result


def test_improvement_brief_tool_max_uses_is_set(mocker):
    """max_uses=5 must be present on the web_search tool definition (Issue 350).
    Without it the model can call web_search unboundedly, blowing token budgets.
    """
    import improvement.brief as b

    captured: dict = {}

    def _create(**kwargs):
        captured.update(kwargs)
        return _Resp([_Block("text", "FINAL")])

    mock_client = mocker.MagicMock()
    mock_client.messages.create.side_effect = _create
    mocker.patch.object(b._ANTHROPIC, "with_options", return_value=mock_client)

    b.generate_improvement_brief(channel_title="Ch", analytics={})

    tools = captured["tools"]
    assert tools[0].get("max_uses") == 5, (
        "web_search tool must carry max_uses=5 to bound unbounded search rounds (Issue 350)"
    )
