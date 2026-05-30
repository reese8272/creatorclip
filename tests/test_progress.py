"""Unit tests for the worker.progress module (Issue 86).

These tests hit a real Redis at REDIS_URL (already required by conftest.py for
the slowapi limiter). Each test scopes its keys with a unique uuid prefix so
runs don't collide.
"""

from __future__ import annotations

import json
import uuid

import pytest
import redis

from config import settings
from worker import progress


@pytest.fixture(autouse=True)
def _reset_module_clients() -> None:
    """Force fresh client singletons per test — keeps state from bleeding."""
    progress._SYNC = None  # noqa: SLF001
    progress._AIO = None  # noqa: SLF001
    yield
    progress._SYNC = None  # noqa: SLF001
    progress._AIO = None  # noqa: SLF001


@pytest.fixture
def task_id() -> str:
    """Unique per-test task id so streams/owners don't collide."""
    return f"test-{uuid.uuid4()}"


@pytest.fixture
def creator_id() -> str:
    return f"creator-{uuid.uuid4()}"


@pytest.fixture
def _cleanup_keys(task_id: str, creator_id: str) -> None:
    """Remove the test's redis keys after the run."""
    yield
    client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    client.delete(
        f"task:{task_id}:events",
        f"task:{task_id}:owner",
        f"sse:count:{creator_id}",
    )


# ── sync_emit ────────────────────────────────────────────────────────────────


def test_sync_emit_writes_xadd_entry(task_id: str, _cleanup_keys: None) -> None:
    progress.sync_emit(task_id, "step", label="fetch_analytics", count=10)
    client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    entries = client.xrange(f"task:{task_id}:events", "-", "+")
    assert len(entries) == 1
    _, fields = entries[0]
    assert fields["type"] == "step"
    assert "ts" in fields
    assert "request_id" in fields
    data = json.loads(fields["data"])
    assert data == {"label": "fetch_analytics", "count": 10}


def test_sync_emit_terminal_event_sets_expiry(task_id: str, _cleanup_keys: None) -> None:
    progress.sync_emit(task_id, "step", label="working")
    client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    # Non-terminal events should not set TTL
    assert client.ttl(f"task:{task_id}:events") == -1
    progress.sync_emit(task_id, "done", brief_len=420)
    ttl = client.ttl(f"task:{task_id}:events")
    assert 3500 < ttl <= 3600


def test_sync_emit_error_also_sets_expiry(task_id: str, _cleanup_keys: None) -> None:
    progress.sync_emit(task_id, "error", message="insufficient data")
    client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    ttl = client.ttl(f"task:{task_id}:events")
    assert 3500 < ttl <= 3600


def test_sync_emit_swallows_redis_errors(task_id: str, monkeypatch) -> None:
    """A Redis hiccup must NOT take down the worker — progress is observational."""

    class _BrokenClient:
        def xadd(self, *a, **kw):
            raise redis.ConnectionError("simulated outage")

        def expire(self, *a, **kw):
            raise redis.ConnectionError("simulated outage")

    monkeypatch.setattr(progress, "_sync_client", lambda: _BrokenClient())
    # Must not raise — the actual worker can't tolerate progress-emission failures
    progress.sync_emit(task_id, "step", label="x")
    progress.sync_emit(task_id, "done")


# ── aemit ────────────────────────────────────────────────────────────────────


async def test_aemit_writes_xadd_entry(task_id: str, _cleanup_keys: None) -> None:
    await progress.aemit(task_id, "step", label="embed")
    client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    entries = client.xrange(f"task:{task_id}:events", "-", "+")
    assert len(entries) == 1
    assert entries[0][1]["type"] == "step"


async def test_aemit_done_sets_expiry(task_id: str, _cleanup_keys: None) -> None:
    await progress.aemit(task_id, "done")
    client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    ttl = client.ttl(f"task:{task_id}:events")
    assert 3500 < ttl <= 3600


# ── ownership ────────────────────────────────────────────────────────────────


async def test_set_and_get_owner(task_id: str, creator_id: str, _cleanup_keys: None) -> None:
    assert await progress.aget_owner(task_id) is None
    await progress.aset_owner(task_id, creator_id)
    assert await progress.aget_owner(task_id) == creator_id


async def test_owner_key_has_expiry(task_id: str, creator_id: str, _cleanup_keys: None) -> None:
    await progress.aset_owner(task_id, creator_id)
    client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    ttl = client.ttl(f"task:{task_id}:owner")
    assert 3500 < ttl <= 3600


# ── replay ───────────────────────────────────────────────────────────────────


async def test_aread_since_returns_committed_events(task_id: str, _cleanup_keys: None) -> None:
    await progress.aemit(task_id, "step", label="a")
    await progress.aemit(task_id, "step", label="b")
    # block_ms=100 just to keep the test fast (events are already there)
    events = await progress.aread_since(task_id, last_id="0-0", block_ms=100)
    assert len(events) == 2
    labels = [json.loads(fields["data"])["label"] for _, fields in events]
    assert labels == ["a", "b"]


async def test_aread_since_cursor_advances(task_id: str, _cleanup_keys: None) -> None:
    await progress.aemit(task_id, "step", label="first")
    first_batch = await progress.aread_since(task_id, last_id="0-0", block_ms=100)
    cursor = first_batch[-1][0]

    await progress.aemit(task_id, "step", label="second")
    second_batch = await progress.aread_since(task_id, last_id=cursor, block_ms=100)
    assert len(second_batch) == 1
    assert json.loads(second_batch[0][1]["data"])["label"] == "second"


async def test_aread_since_empty_when_no_events(task_id: str, _cleanup_keys: None) -> None:
    """block_ms=100 + no writers = empty list (NOT a hang). Caller sends keepalive."""
    events = await progress.aread_since(task_id, last_id="0-0", block_ms=100)
    assert events == []


# ── concurrent slot cap ──────────────────────────────────────────────────────


async def test_aacquire_slot_under_cap_succeeds(creator_id: str, _cleanup_keys: None) -> None:
    assert await progress.aacquire_slot(creator_id, max_concurrent=3) is True
    assert await progress.aacquire_slot(creator_id, max_concurrent=3) is True


async def test_aacquire_slot_blocks_over_cap(creator_id: str, _cleanup_keys: None) -> None:
    for _ in range(3):
        assert await progress.aacquire_slot(creator_id, max_concurrent=3) is True
    # 4th call must be refused
    assert await progress.aacquire_slot(creator_id, max_concurrent=3) is False


async def test_arelease_slot_frees_capacity(creator_id: str, _cleanup_keys: None) -> None:
    for _ in range(3):
        await progress.aacquire_slot(creator_id, max_concurrent=3)
    assert await progress.aacquire_slot(creator_id, max_concurrent=3) is False
    await progress.arelease_slot(creator_id)
    # Now we should be able to re-acquire
    assert await progress.aacquire_slot(creator_id, max_concurrent=3) is True


# ── serialization invariants ─────────────────────────────────────────────────


def test_serialize_includes_request_id_from_contextvar(monkeypatch) -> None:
    from observability import request_id_ctx

    request_id_ctx.set("req-abc-123")
    payload = progress._serialize("step", {"label": "x"})  # noqa: SLF001
    assert payload["request_id"] == "req-abc-123"
    assert payload["type"] == "step"
    assert json.loads(payload["data"]) == {"label": "x"}


def test_serialize_handles_non_json_values() -> None:
    payload = progress._serialize("step", {"when": __import__("datetime").date(2026, 5, 30)})  # noqa: SLF001
    # Should not raise — default=str converts non-JSON types
    data = json.loads(payload["data"])
    assert data == {"when": "2026-05-30"}
