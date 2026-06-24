"""
Unit tests for Issue 57 — automatic refund on terminal ingest failure.

Predicate logic only; the end-to-end DB scenario lives in
`tests/test_billing_refund_integration.py`.
"""

import inspect
import uuid
from unittest.mock import MagicMock, patch

import db
from billing import refund as refund_mod
from billing.refund import _refund_pack_id

# ── pack_id construction ──────────────────────────────────────────────────────


def test_refund_pack_id_is_video_keyed():
    vid = uuid.uuid4()
    assert _refund_pack_id(vid) == f"refund:{vid}"


def test_refund_pack_id_is_unique_per_video():
    a, b = uuid.uuid4(), uuid.uuid4()
    assert _refund_pack_id(a) != _refund_pack_id(b)


# ── RefundOnFailureTask.on_failure dispatch ───────────────────────────────────


def _make_task_instance():
    from worker.tasks import RefundOnFailureTask

    task = RefundOnFailureTask()
    return task


def test_on_failure_dispatches_refund_with_uuid():
    task = _make_task_instance()
    video_id = str(uuid.uuid4())

    with patch("worker.tasks.run_async") as mock_run:
        task.on_failure(
            exc=RuntimeError("boom"),
            task_id="task-1",
            args=(video_id,),
            kwargs={},
            einfo=None,
        )

    # on_failure now dispatches TWO coroutines: refund_for_video (line ~128) AND
    # _fire_refund_notification_async (line ~144, added with the refund-notification
    # wiring). Both go through run_async. (Previously asserted 1 — stale.)
    assert mock_run.call_count == 2


def test_on_failure_noop_on_missing_video_id():
    task = _make_task_instance()
    with patch("worker.tasks.run_async") as mock_run:
        task.on_failure(
            exc=RuntimeError("boom"),
            task_id="task-1",
            args=(),
            kwargs={},
            einfo=None,
        )
    mock_run.assert_not_called()


def test_on_failure_noop_on_malformed_video_id():
    task = _make_task_instance()
    with patch("worker.tasks.run_async") as mock_run:
        task.on_failure(
            exc=RuntimeError("boom"),
            task_id="task-1",
            args=("not-a-uuid",),
            kwargs={},
            einfo=None,
        )
    mock_run.assert_not_called()


def test_on_failure_swallows_refund_exception():
    """A crash in the refund path must NOT crash the failure-handling path —
    the task's original terminal failure must still stand."""
    task = _make_task_instance()
    with patch("worker.tasks.run_async", side_effect=RuntimeError("refund crashed")):
        # Must not raise.
        task.on_failure(
            exc=RuntimeError("ingest failed"),
            task_id="task-1",
            args=(str(uuid.uuid4()),),
            kwargs={},
            einfo=None,
        )


# Note: the refund_for_video idempotency / no-op branches are covered
# end-to-end against a real Postgres in
# `tests/test_billing_refund_integration.py` — mock-based async unit tests for
# those branches are fragile (the `async with db.AdminSessionLocal()` context
# manager is awkward to fake) and offer no signal the integration tests don't.


# ── RLS session-factory invariant (Wave 1 hotfix B) ──────────────────────────
#
# `refund_for_video` is a system action invoked by Celery's `on_failure`
# callback with no creator context to inject into `session.info["creator_id"]`.
# Under the prod RLS role split (Issue 79), an app-role session without that
# key returns ZERO rows from the `MinuteDeduction` SELECT — silently no-opping
# every refund. The fix is structural: use `AdminSessionLocal()` (BYPASSRLS)
# to match every other worker-surface session. These tests pin that choice so
# a future "factory consolidation" can't silently re-break it.


def test_refund_uses_admin_session_factory_source_inspect():
    """Source-text invariant: refund_for_video must reference AdminSessionLocal,
    not AsyncSessionLocal. Cheap, robust, and survives an import refactor."""
    src = inspect.getsource(refund_mod.refund_for_video)
    assert "AdminSessionLocal" in src, (
        "refund_for_video must use db.AdminSessionLocal (BYPASSRLS) — "
        "see Wave 1 hotfix B / /assess REPORT row 2"
    )
    assert "AsyncSessionLocal" not in src, (
        "refund_for_video must NOT use db.AsyncSessionLocal — under prod RLS "
        "without session.info['creator_id'] the SELECT returns zero rows"
    )


async def test_refund_for_video_calls_admin_factory():
    """Runtime invariant: even with the import unchanged, the factory actually
    called is AdminSessionLocal. Guards against future aliasing higher up the
    file (e.g. ``SessionLocal = db.AsyncSessionLocal``) that source-inspect
    might miss. Fully mocked — no Postgres needed."""

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def scalar(self, *_a, **_kw):
            # Returning None makes refund_for_video short-circuit at the
            # "no deduction" branch — exactly what we want for observing
            # which factory was opened.
            return None

        async def commit(self):
            pass

    # `db.AdminSessionLocal()` is invoked synchronously by the `async with`
    # statement (it must return a context manager, not a coroutine), so the
    # factory itself is a regular sync MagicMock, not AsyncMock.
    admin_factory = MagicMock(return_value=_FakeSession())
    app_factory = MagicMock(return_value=_FakeSession())

    with (
        patch.object(db, "AdminSessionLocal", admin_factory),
        patch.object(db, "AsyncSessionLocal", app_factory),
    ):
        result = await refund_mod.refund_for_video(uuid.uuid4())

    assert result == 0, "no-deduction branch must return 0"
    assert admin_factory.call_count == 1, "refund must open exactly one AdminSessionLocal"
    assert app_factory.call_count == 0, "refund must NEVER open an AsyncSessionLocal (RLS-gated)"
