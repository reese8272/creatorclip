"""
Celery Beat periodic schedule.

poll_clip_outcomes runs every hour: finds clips past the 48h and 7d post-publish
checkpoints, fetches YouTube stats, and sets performed_well on ClipOutcome.

purge_stale_source_media runs every hour: deletes source video files older than
SOURCE_MEDIA_RETENTION_HOURS to comply with YouTube ToS data-retention requirements.

refresh_youtube_analytics runs daily: re-fetches video metrics and audience data
for all creators to keep analytics current.

purge_stale_youtube_analytics runs daily (Wave-4 Fix 3 / Issue 75b): deletes
analytics rows whose ``fetched_at`` exceeds ``YOUTUBE_ANALYTICS_MAX_STALENESS_DAYS``
(default 30) — the hard cutoff in YouTube API Services Developer Policies
§III.E.4.b for when authorization cannot be re-verified. Runs 6 hours offset
from refresh_youtube_analytics so the purge sees the FRESHEST possible
fetched_at values for healthy creators and only sweeps genuinely-stale rows.
"""

from datetime import timedelta

from worker.celery_app import celery  # noqa: F401

celery.conf.beat_schedule = {
    # Issue 196 — sweep due confirmed scheduled publications every 5 minutes and
    # enqueue publish_to_youtube for each. Advisory lock inside the task body
    # ensures only one sweep runs at a time across multi-worker deploys.
    # 5-minute granularity matches the practical precision of upload-timing
    # windows (reported as hour-of-day, not sub-minute).
    "sweep-scheduled-publications": {
        "task": "worker.tasks.sweep_scheduled_publications",
        "schedule": timedelta(minutes=5),
    },
    "poll-clip-outcomes-hourly": {
        "task": "worker.tasks.poll_clip_outcomes",
        "schedule": timedelta(hours=1),
    },
    "purge-stale-source-media-hourly": {
        "task": "worker.tasks.purge_stale_source_media",
        "schedule": timedelta(hours=1),
    },
    "refresh-youtube-analytics-daily": {
        "task": "worker.tasks.refresh_youtube_analytics",
        "schedule": timedelta(hours=24),
    },
    "purge-stale-youtube-analytics-daily": {
        "task": "worker.tasks.purge_stale_youtube_analytics",
        "schedule": timedelta(hours=24),
    },
    # Issue 126 — daily observability sweep for trial expirations. Watchdog
    # only: logs creators whose trial just ended with zero balance so we can
    # see funnel drop-off. State enforcement (402 paywall) lives in
    # billing/ledger.py and reads trial_ends_at live — there is no flag this
    # task flips.
    "expire-trials-daily": {
        "task": "worker.tasks.expire_trials",
        "schedule": timedelta(hours=24),
    },
    # Issue 205 — daily Stripe↔ledger reconciliation. Catches paid Checkout
    # sessions whose webhook was never delivered (Stripe outage / endpoint down
    # past the retry window) by sweeping the Sessions list API and granting
    # minutes for any paid session missing a MinutePack row. Idempotent: the
    # existing UNIQUE(stripe_session_id) + SAVEPOINT dedup in grant_minutes()
    # makes re-runs and concurrent deliveries safe. Runs 12h offset from the
    # daily analytics refresh to spread DB load.
    "reconcile-stripe-ledger-daily": {
        "task": "worker.tasks.reconcile_stripe_ledger",
        "schedule": timedelta(hours=24),
    },
    # Issue 250 — GDPR Art. 5(1)(e) storage-limitation: 90-day rolling purge
    # of behavioral telemetry in the event_logs table. event_logs has no FK to
    # creators, so per-creator erasure (Issue 248) is handled separately by
    # event_log.purge_creator_events(); this task sweeps time-expired rows
    # across all creators. Best-effort: failures are logged and swallowed
    # inside event_log.purge_stale_events().
    "purge-stale-event-logs-daily": {
        "task": "worker.tasks.purge_stale_event_logs",
        "schedule": timedelta(hours=24),
    },
}
