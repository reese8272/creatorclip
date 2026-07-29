"""Tests for routers/_enqueue.py (ready-pass W2 — routers-DRY lane).

80/20: the default-key happy path, the domain-key override, and the load-bearing
fail-open contract (RedisError → stream_url=None, never a raised error).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import redis

from routers._enqueue import enqueue_stream_task, stamp_stream_owner


def _task_fn(task_id: str) -> MagicMock:
    fn = MagicMock()
    fn.delay.return_value = MagicMock(id=task_id)
    return fn


async def test_enqueue_defaults_stream_key_to_task_id() -> None:
    fn = _task_fn("t-123")
    with patch("worker.progress.aset_owner", new=AsyncMock()) as aset:
        task, url = await enqueue_stream_task(
            fn, "arg-a", "arg-b", creator_id="c-1", log_label="test"
        )
    fn.delay.assert_called_once_with("arg-a", "arg-b")
    assert task.id == "t-123"
    assert url == "/tasks/t-123/events"
    aset.assert_awaited_once_with("t-123", "c-1")


async def test_explicit_stream_key_overrides_task_id() -> None:
    fn = _task_fn("t-123")
    with patch("worker.progress.aset_owner", new=AsyncMock()) as aset:
        _task, url = await enqueue_stream_task(
            fn, "arg", creator_id="c-1", stream_key="clip-9", log_label="test"
        )
    assert url == "/tasks/clip-9/events"
    aset.assert_awaited_once_with("clip-9", "c-1")


async def test_redis_error_fails_open_with_none_stream_url() -> None:
    boom = AsyncMock(side_effect=redis.RedisError("down"))
    with patch("worker.progress.aset_owner", new=boom):
        url = await stamp_stream_owner("key-1", "c-1", log_label="test")
    assert url is None
