"""Integration tests for the SSE endpoint at /tasks/{task_id}/events (Issue 86).

Uses TestClient.stream() to consume the streaming response. Auth is forced via
``app.dependency_overrides[get_current_creator]`` — the same pattern other
tests in this repo use (see test_videos_upload_streaming.py).
"""

from __future__ import annotations

import json
import time
import uuid
from unittest.mock import MagicMock

import pytest
import redis

from auth import get_current_creator
from config import settings
from main import app
from tests._helpers import override_current_creator
from worker import progress


@pytest.fixture(autouse=True)
def _reset_redis_singletons() -> None:
    """Fresh singletons per test so background Redis state doesn't leak."""
    progress._SYNC = None  # noqa: SLF001
    progress._AIO = None  # noqa: SLF001
    progress._AIO_LOOP = None  # noqa: SLF001
    yield
    progress._SYNC = None  # noqa: SLF001
    progress._AIO = None  # noqa: SLF001
    progress._AIO_LOOP = None  # noqa: SLF001


@pytest.fixture
def task_id() -> str:
    return f"sse-test-{uuid.uuid4()}"


@pytest.fixture
def creator_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def _fake_creator(creator_id: str) -> MagicMock:
    """A minimal Creator stand-in — the endpoint only uses .id."""
    creator = MagicMock()
    creator.id = uuid.UUID(creator_id)
    return creator


@pytest.fixture
def _cleanup(task_id: str, creator_id: str) -> None:
    """Wipe the test's redis keys after the run."""
    yield
    client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    client.delete(
        f"task:{task_id}:events",
        f"task:{task_id}:owner",
        f"sse:count:{creator_id}",
    )


def _set_owner_sync(task_id: str, creator_id: str) -> None:
    """Helper: set the ownership key via sync redis (avoids needing the async client)."""
    client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    client.set(f"task:{task_id}:owner", creator_id, ex=3600)


def _seed_event(task_id: str, event_type: str, **fields) -> None:
    """Helper: push an event onto the task's stream synchronously."""
    progress.sync_emit(task_id, event_type, **fields)


def _override_auth(creator) -> None:
    app.dependency_overrides[get_current_creator] = override_current_creator(creator)


def _clear_auth() -> None:
    app.dependency_overrides.pop(get_current_creator, None)


# ── Auth + ownership gates ───────────────────────────────────────────────────


def test_unknown_task_returns_404(client, _fake_creator, _cleanup, task_id) -> None:
    _override_auth(_fake_creator)
    try:
        r = client.get(f"/tasks/{task_id}/events")
        assert r.status_code == 404
        assert "Unknown task" in r.json()["detail"]
    finally:
        _clear_auth()


def test_cross_creator_returns_403(client, _fake_creator, _cleanup, task_id, creator_id) -> None:
    # Owned by SOMEONE ELSE
    other = str(uuid.uuid4())
    _set_owner_sync(task_id, other)
    _override_auth(_fake_creator)
    try:
        r = client.get(f"/tasks/{task_id}/events")
        assert r.status_code == 403
        assert "Not your task" in r.json()["detail"]
    finally:
        _clear_auth()


def test_missing_auth_returns_401(client, _cleanup, task_id) -> None:
    # No dependency override → real get_current_creator runs → no session cookie → 401
    r = client.get(f"/tasks/{task_id}/events")
    assert r.status_code == 401


# ── Replay + Last-Event-ID resume ────────────────────────────────────────────


def test_replays_existing_events_on_connect(
    client, _fake_creator, _cleanup, task_id, creator_id
) -> None:
    _set_owner_sync(task_id, creator_id)
    _seed_event(task_id, "step", label="acquire_lock")
    _seed_event(task_id, "step", label="analyze_patterns", videos=10)
    _seed_event(task_id, "done", brief_chars=420)

    _override_auth(_fake_creator)
    try:
        with client.stream("GET", f"/tasks/{task_id}/events") as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            assert resp.headers["cache-control"] == "no-cache"
            assert resp.headers["x-accel-buffering"] == "no"

            events = _collect_sse_events(resp, max_events=3, timeout=5.0)
    finally:
        _clear_auth()

    types = [e["event"] for e in events]
    assert types == ["step", "step", "done"]
    assert events[0]["data"]["label"] == "acquire_lock"
    assert events[2]["data"]["brief_chars"] == 420


def test_terminal_event_closes_stream(client, _fake_creator, _cleanup, task_id, creator_id) -> None:
    _set_owner_sync(task_id, creator_id)
    _seed_event(task_id, "step", label="x")
    _seed_event(task_id, "error", message="insufficient data")

    _override_auth(_fake_creator)
    try:
        with client.stream("GET", f"/tasks/{task_id}/events") as resp:
            events = _collect_sse_events(resp, max_events=2, timeout=5.0)
            # The generator returns after the terminal event — body should be
            # fully consumed and no further events arrive.
            assert events[-1]["event"] == "error"
    finally:
        _clear_auth()


def test_last_event_id_resumes_from_cursor(
    client, _fake_creator, _cleanup, task_id, creator_id
) -> None:
    _set_owner_sync(task_id, creator_id)
    _seed_event(task_id, "step", label="first")
    _seed_event(task_id, "step", label="second")
    _seed_event(task_id, "done")

    # First connect: read first event + capture its id
    _override_auth(_fake_creator)
    try:
        with client.stream("GET", f"/tasks/{task_id}/events") as resp:
            events = _collect_sse_events(resp, max_events=1, timeout=5.0)
        first_id = events[0]["id"]

        # Reconnect with Last-Event-ID = first_id; should skip first event,
        # replay second + done from the cursor.
        with client.stream(
            "GET",
            f"/tasks/{task_id}/events",
            headers={"Last-Event-ID": first_id},
        ) as resp:
            events = _collect_sse_events(resp, max_events=2, timeout=5.0)
        assert [e["data"]["label"] for e in events if e["event"] == "step"] == ["second"]
        assert events[-1]["event"] == "done"
    finally:
        _clear_auth()


# ── Concurrent cap ───────────────────────────────────────────────────────────


def test_concurrent_cap_yields_error_event(
    client, _fake_creator, _cleanup, task_id, creator_id
) -> None:
    _set_owner_sync(task_id, creator_id)
    # Pre-fill the per-creator counter to the cap via the sync client. Using
    # the async client here would bind it to this test's loop; the SSE endpoint
    # then runs on TestClient's loop and hits "Future attached to a different
    # loop." Sync Redis sidesteps the issue entirely.
    redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    redis_client.set(f"sse:count:{creator_id}", 3, ex=3600)

    _override_auth(_fake_creator)
    try:
        with client.stream("GET", f"/tasks/{task_id}/events") as resp:
            events = _collect_sse_events(resp, max_events=1, timeout=5.0)
        assert events[0]["event"] == "error"
        assert "too many" in events[0]["data"]["message"].lower()
    finally:
        _clear_auth()


# ── SSE wire-format parser ───────────────────────────────────────────────────


def _collect_sse_events(response, max_events: int, timeout: float) -> list[dict]:
    """Parse `id:`/`event:`/`data:` lines into structured events.

    Returns after collecting `max_events` events. Skips keepalive comments
    (`: keepalive\\n\\n`). Times out after `timeout` seconds total.
    """
    events: list[dict] = []
    current: dict = {}
    buffer = ""

    deadline = time.monotonic() + timeout
    for chunk in response.iter_text():
        if time.monotonic() > deadline:
            break
        buffer += chunk
        while "\n\n" in buffer:
            raw_event, buffer = buffer.split("\n\n", 1)
            if not raw_event.strip() or raw_event.startswith(":"):
                # Keepalive comment, skip
                continue
            for line in raw_event.split("\n"):
                if line.startswith("id:"):
                    current["id"] = line[len("id:") :].strip()
                elif line.startswith("event:"):
                    current["event"] = line[len("event:") :].strip()
                elif line.startswith("data:"):
                    raw = line[len("data:") :].strip()
                    try:
                        current["data"] = json.loads(raw)
                    except json.JSONDecodeError:
                        current["data"] = {"_raw": raw}
            if current:
                events.append(current)
                current = {}
                if len(events) >= max_events:
                    return events
    return events
