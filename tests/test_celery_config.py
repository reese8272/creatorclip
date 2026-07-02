"""
Unit tests for the Celery reliability configuration (Issue 62).

DB-free — guards the at-least-once invariants so a future config edit can't
silently reintroduce dropped tasks or duplicate execution.
"""

from worker.celery_app import HARD_LIMIT_MARGIN_S, celery, visibility_timeout_s


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


def test_visibility_timeout_derived_from_soft_limit():
    # Config-derivation invariant (Issue 352 Batch F): visibility_timeout is
    # computed from the soft limit, so soft < hard (= soft + margin) < visibility
    # holds for ANY CELERY_SOFT_TIME_LIMIT_S — including values past the old
    # hardcoded 3600, which used to silently allow Redis mid-run redelivery.
    assert visibility_timeout_s(3000) == 3600  # default keeps the 3600 floor
    for soft in (60, 3000, 3300, 3600, 7200, 24 * 3600):
        assert soft < soft + HARD_LIMIT_MARGIN_S < visibility_timeout_s(soft)


def test_conf_visibility_timeout_uses_derivation():
    from config import settings

    assert celery.conf.broker_transport_options["visibility_timeout"] == visibility_timeout_s(
        settings.CELERY_SOFT_TIME_LIMIT_S
    )
    assert celery.conf.task_time_limit == settings.CELERY_SOFT_TIME_LIMIT_S + HARD_LIMIT_MARGIN_S


def test_prefetch_multiplier_one_for_long_jobs():
    # One task per worker at a time — long media jobs must not be hoarded/starved.
    assert celery.conf.worker_prefetch_multiplier == 1
