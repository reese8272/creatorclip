"""
Issue 39 — per-worker singleton event loop + engine rebind.

These tests pin the contract that lets Celery workers safely use the async
SQLAlchemy engine: one loop per worker process, engine rebound to that loop
on worker_process_init, disposed on worker_process_shutdown.
"""

import asyncio

import pytest

import db
import worker.celery_app as celery_app


@pytest.fixture
def isolated_worker_loop(monkeypatch):
    """Install a fresh _LOOP into worker.celery_app for the duration of a test
    and restore the previous state on teardown so other tests are unaffected."""
    previous = celery_app._LOOP
    loop = asyncio.new_event_loop()
    monkeypatch.setattr(celery_app, "_LOOP", loop)
    try:
        yield loop
    finally:
        if not loop.is_closed():
            loop.close()
        monkeypatch.setattr(celery_app, "_LOOP", previous)


def test_run_async_reuses_loop_across_calls(isolated_worker_loop):
    """Two run_async calls in the same 'worker process' resolve on the same loop.
    This is the invariant that prevents the asyncpg pool from being rebound to a
    new loop on every task."""

    async def get_loop():
        return asyncio.get_running_loop()

    first = celery_app.run_async(get_loop())
    second = celery_app.run_async(get_loop())

    assert first is isolated_worker_loop
    assert second is isolated_worker_loop


def test_run_async_falls_back_when_no_worker_loop():
    """Outside a worker (no _LOOP installed) run_async still executes the coro
    via asyncio.run. Keeps unit tests that call task bodies directly working."""

    async def echo():
        return "ok"

    assert celery_app._LOOP is None
    assert celery_app.run_async(echo()) == "ok"


def test_worker_process_init_installs_loop_and_rebinds_engine(monkeypatch):
    """worker_process_init hook creates the singleton loop and calls
    db.recreate_engine so the asyncpg pool is bound to this process's loop."""
    previous_loop = celery_app._LOOP
    monkeypatch.setattr(celery_app, "_LOOP", None)

    recreated: list[bool] = []
    monkeypatch.setattr(db, "recreate_engine", lambda: recreated.append(True))

    try:
        celery_app._init_worker_loop()
        assert celery_app._LOOP is not None
        assert not celery_app._LOOP.is_closed()
        assert recreated == [True]
    finally:
        if celery_app._LOOP is not None and not celery_app._LOOP.is_closed():
            celery_app._LOOP.close()
        monkeypatch.setattr(celery_app, "_LOOP", previous_loop)


def test_worker_process_shutdown_disposes_engine_and_closes_loop(monkeypatch):
    """worker_process_shutdown disposes the engine on the worker loop and
    closes the loop."""
    previous_loop = celery_app._LOOP
    loop = asyncio.new_event_loop()
    monkeypatch.setattr(celery_app, "_LOOP", loop)

    disposed: list[bool] = []

    async def fake_dispose():
        disposed.append(True)

    monkeypatch.setattr(db, "dispose_engine", fake_dispose)

    try:
        celery_app._shutdown_worker_loop()
        assert disposed == [True]
        assert loop.is_closed()
        assert celery_app._LOOP is None
    finally:
        monkeypatch.setattr(celery_app, "_LOOP", previous_loop)


def test_recreate_engine_swaps_module_globals():
    """recreate_engine replaces both engine and AsyncSessionLocal so callers
    looking them up via db.<attr> at call time see the new objects."""
    pre_engine = db.engine
    pre_factory = db.AsyncSessionLocal

    db.recreate_engine()

    try:
        assert db.engine is not pre_engine
        assert db.AsyncSessionLocal is not pre_factory
    finally:
        # Replace again so any state mutated here doesn't leak into later tests.
        db.recreate_engine()
