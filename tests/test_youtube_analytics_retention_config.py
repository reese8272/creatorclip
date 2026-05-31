"""Pins the YouTube analytics retention setting to the ToS-mandated value.

Per YouTube API Services Developer Policies §III.E.4.b + §III.D.2.3.b
(verified 2026-05-31 via industry-standards-researcher), the maximum allowed
window for caching YouTube API Data without re-verifying authorization is
**30 calendar days**. Lengthening past 30 is a documented ToS violation.

This unit test runs in the default lane (no DB needed) so any future
config-default drift past 30 days is caught loudly at every PR cycle, not
silently in production.
"""

import inspect


def test_youtube_analytics_max_staleness_default_is_30_days():
    """YOUTUBE_ANALYTICS_MAX_STALENESS_DAYS must default to 30 — the exact
    number in YouTube API Services Developer Policies §III.E.4.b."""
    from config import Settings

    s = Settings()
    assert s.YOUTUBE_ANALYTICS_MAX_STALENESS_DAYS == 30, (
        f"Wave-4 Fix 3 / Issue 75b: YOUTUBE_ANALYTICS_MAX_STALENESS_DAYS "
        f"must default to 30 (YouTube ToS §III.E.4.b). Got "
        f"{s.YOUTUBE_ANALYTICS_MAX_STALENESS_DAYS}. Lengthening past 30 is a "
        f"documented ToS violation. Source: "
        f"https://developers.google.com/youtube/terms/developer-policies"
    )
    # Bounded sanity check: must be a positive integer.
    assert isinstance(s.YOUTUBE_ANALYTICS_MAX_STALENESS_DAYS, int)
    assert s.YOUTUBE_ANALYTICS_MAX_STALENESS_DAYS > 0


def test_config_setting_cites_youtube_tos_source():
    """The config setting's docstring/comment must cite the source URL so a
    future contributor changing the number sees the rationale + can verify
    the policy hasn't shifted. Pins the explanatory comment to the file."""
    import config

    src = inspect.getsource(config)
    assert "YOUTUBE_ANALYTICS_MAX_STALENESS_DAYS" in src
    # Either the URL OR the §III.E.4.b citation must be present.
    assert "developer-policies" in src or "III.E.4.b" in src, (
        "The YOUTUBE_ANALYTICS_MAX_STALENESS_DAYS setting must cite the "
        "YouTube ToS source (URL or §III.E.4.b) so future readers can "
        "verify the 30-day requirement before changing it."
    )


def test_purge_task_registered_in_beat_schedule():
    """The new purge task must be wired into the Celery Beat schedule —
    without this, the policy compliance is documented but not enforced.
    Source-inspect test — the actual Beat scheduler is integration-tested
    by Celery itself."""
    import inspect

    import worker.schedule as schedule_module

    src = inspect.getsource(schedule_module)
    assert "purge_stale_youtube_analytics" in src, (
        "Wave-4 Fix 3: worker/schedule.py must register the "
        "purge_stale_youtube_analytics Beat task — otherwise the policy "
        "compliance is configured but never runs."
    )
