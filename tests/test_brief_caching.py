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
