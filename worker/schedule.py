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
}
