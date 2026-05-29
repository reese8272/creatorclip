"""
Unit tests for Issue 57 — automatic refund on terminal ingest failure.

Predicate logic only; the end-to-end DB scenario lives in
`tests/test_billing_refund_integration.py`.
"""

import uuid
from unittest.mock import patch

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

    assert mock_run.call_count == 1


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
# those branches are fragile (the `async with db.AsyncSessionLocal()` context
# manager is awkward to fake) and offer no signal the integration tests don't.
