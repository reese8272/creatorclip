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
    import dna.brief as b

    captured: dict = {}

    def _create(**kwargs):
        captured.update(kwargs)
        return _Resp([_Block("text", "BRIEF BODY")])

    mocker.patch.object(b._ANTHROPIC.messages, "create", side_effect=_create)

    out = b.generate_brief({"top_videos": [{"title": "T", "hook_text": "H"}]}, "Acme Channel")

    system = captured["system"]
    assert len(system) == 2
    # Block 0: static, cached, contains NO per-creator data.
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    assert "Acme Channel" not in system[0]["text"]
    assert "CREATOR PERFORMANCE DATA" not in system[0]["text"]
    # Block 1: volatile, NOT cached, carries the creator data.
    assert "cache_control" not in system[1]
    assert "Acme Channel" in system[1]["text"]
    assert out.startswith("BRIEF BODY")


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

    out = b.generate_improvement_brief(channel_title="Ch", analytics={"avg_views": 100})

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
