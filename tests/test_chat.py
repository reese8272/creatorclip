"""Unit tests for the Pro chatbot (Issue 152) — no DB, no live Anthropic.

Covers the load-bearing, non-DB pieces:
- honesty constraint is structurally present in the system prompt (CLAUDE.md);
- tool schemas never expose a creator_id parameter (the worker injects it);
- the access gate's active-creator predicate;
- the agentic loop: tool round-trips, the iteration cap, and usage summing.

DB-backed per-creator isolation is verified in
tests/test_chat_isolation_integration.py (CI / real Postgres).
"""

import types
import uuid

import pytest

from chat.prompt import HONESTY_CONSTRAINT, build_system
from chat.tools import _EXECUTORS, TOOLS
from knowledge.util import UNTRUSTED_CONTENT_POLICY


def test_system_prompt_carries_honesty_constraint():
    blocks = build_system("My Channel")
    text = " ".join(b["text"] for b in blocks)
    lowered = text.lower()
    # The verbatim honesty constraint and the explicit prohibition are present —
    # the prompt forbids any virality promise (it mentions "viral" only to ban it).
    assert HONESTY_CONSTRAINT in text
    assert "does not promise virality" in lowered
    assert "never" in lowered and "viral" in lowered


def test_system_prompt_caches_stable_prefix():
    blocks = build_system("My Channel")
    # First (stable) block carries the cache breakpoint; per-creator channel
    # label is a separate uncached block so it can't break the shared prefix.
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in blocks[1]


def test_system_prompt_without_channel_is_single_block():
    blocks = build_system(None)
    assert len(blocks) == 1


def test_system_prompt_carries_untrusted_content_policy():
    """Issue 225 AC: UNTRUSTED_CONTENT_POLICY must appear in build_system output."""
    blocks = build_system("My Channel")
    text = " ".join(b["text"] for b in blocks)
    assert UNTRUSTED_CONTENT_POLICY in text, (
        "chat/prompt.py build_system output must contain UNTRUSTED_CONTENT_POLICY. (Issue 225)"
    )


def test_tools_expose_no_creator_id_and_match_executors():
    names = {t["name"] for t in TOOLS}
    assert names == set(_EXECUTORS)  # every schema has an executor and vice-versa
    for tool in TOOLS:
        props = tool["input_schema"].get("properties", {})
        assert "creator_id" not in props, f"{tool['name']} must not take creator_id"
        assert tool["input_schema"].get("additionalProperties") is False


def _creator(*, balance: int, trial_ends_at):
    return types.SimpleNamespace(minutes_balance=balance, trial_ends_at=trial_ends_at)


def test_chat_access_gate():
    from datetime import UTC, datetime, timedelta

    from fastapi import HTTPException

    from routers.chat import _require_chat_access

    # Positive balance → allowed.
    _require_chat_access(_creator(balance=10, trial_ends_at=None))
    # Zero balance but live trial → allowed.
    _require_chat_access(_creator(balance=0, trial_ends_at=datetime.now(UTC) + timedelta(days=3)))
    # Zero balance + expired trial → 402.
    with pytest.raises(HTTPException) as exc:
        _require_chat_access(
            _creator(balance=0, trial_ends_at=datetime.now(UTC) - timedelta(days=1))
        )
    assert exc.value.status_code == 402
    # Zero balance + no trial → 402.
    with pytest.raises(HTTPException) as exc2:
        _require_chat_access(_creator(balance=0, trial_ends_at=None))
    assert exc2.value.status_code == 402


# ── Agentic loop ───────────────────────────────────────────────────────────────


def _text_block(text):
    return types.SimpleNamespace(type="text", text=text)


def _tool_block(name, tool_input, _id):
    return types.SimpleNamespace(type="tool_use", name=name, input=tool_input, id=_id)


def _msg(stop_reason, content):
    return types.SimpleNamespace(stop_reason=stop_reason, content=content)


def _usage(**kw):
    base = {"input_tokens": 0, "output_tokens": 0, "cache_read": 0, "cache_creation": 0}
    base.update(kw)
    return base


@pytest.fixture
def _patch_runner(monkeypatch):
    """Patch the LLM call, tool execution, and SSE emit out of chat.runner."""
    calls = {"stream": 0, "tools": []}

    async def _fake_execute(name, tool_input, creator_id, session):
        calls["tools"].append(name)
        return '{"ok": true}', False  # (result_json, failed) — Issue 222

    async def _fake_aemit(*a, **k):
        return None

    monkeypatch.setattr("chat.runner.execute_tool", _fake_execute)
    monkeypatch.setattr("chat.runner.aemit", _fake_aemit)
    return calls, monkeypatch


async def test_runner_executes_tool_then_answers(_patch_runner):
    from chat import runner

    calls, monkeypatch = _patch_runner
    scripted = [
        (_msg("tool_use", [_tool_block("get_recent_videos", {}, "tu_1")]), _usage(output_tokens=5)),
        (_msg("end_turn", [_text_block("Your best video was X.")]), _usage(output_tokens=20)),
    ]

    def _fake_stream(client, task_id, **kwargs):
        calls["stream"] += 1
        return scripted.pop(0)

    monkeypatch.setattr("chat.runner.stream_message", _fake_stream)

    text, usage = await runner.run_chat_turn(
        "task-1", uuid.uuid4(), "My Channel", [{"role": "user", "content": "hi"}], session=None
    )
    assert text == "Your best video was X."
    assert calls["tools"] == ["get_recent_videos"]
    assert calls["stream"] == 2
    assert usage["output_tokens"] == 25  # summed across both round-trips


async def test_runner_caps_tool_iterations(_patch_runner):
    from chat import runner
    from config import settings

    calls, monkeypatch = _patch_runner

    # Model "never stops" calling tools — the loop must still terminate.
    def _always_tool(client, task_id, **kwargs):
        calls["stream"] += 1
        return (
            _msg("tool_use", [_tool_block("get_channel_dna", {}, f"tu_{calls['stream']}")]),
            _usage(),
        )

    monkeypatch.setattr("chat.runner.stream_message", _always_tool)

    text, _ = await runner.run_chat_turn(
        "task-2", uuid.uuid4(), None, [{"role": "user", "content": "go"}], session=None
    )
    # At most MAX_TOOL_ITERATIONS tool rounds + 1 forced-text round.
    assert calls["stream"] == settings.CHAT_MAX_TOOL_ITERATIONS + 1
    assert text == ""  # never produced a text answer, but did not hang
