"""
Unit tests for the Celery reliability configuration (Issue 62).

DB-free — guards the at-least-once invariants so a future config edit can't
silently reintroduce dropped tasks or duplicate execution.
"""

from worker.celery_app import celery


def test_acks_late_paired_with_reject_on_worker_lost():
    # acks_late without reject_on_worker_lost silently drops tasks whose worker dies.
    assert celery.conf.task_acks_late is True
    assert celery.conf.task_reject_on_worker_lost is True


def test_time_limits_below_visibility_timeout():
    # The invariant: soft < hard time limit < broker visibility_timeout, so a task
    # is killed before Redis would redeliver a still-running copy.
    soft = celery.conf.task_soft_time_limit
    hard = celery.conf.task_time_limit
    visibility = celery.conf.broker_transport_options["visibility_timeout"]
    assert soft < hard < visibility


def test_prefetch_multiplier_one_for_long_jobs():
    # One task per worker at a time — long media jobs must not be hoarded/starved.
    assert celery.conf.worker_prefetch_multiplier == 1
