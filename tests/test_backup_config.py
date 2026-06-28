"""Pins the disaster-recovery backup settings (Issue 256).

Default lane (no DB). Locks in: the settings load with safe defaults, daily
retention stays inside the 30-day YouTube analytics-staleness ceiling, and the
backup config is deliberately NOT a production-boot requirement (the API/worker
serving traffic must not crash-loop because a cron-only setting drifted —
backup_pg.sh validates its own env at runtime instead).
"""

import inspect


def test_backup_settings_load_with_safe_defaults():
    from config import Settings

    s = Settings()
    # Disabled-by-default in dev (empty bucket/key → backup_pg.sh fails fast).
    assert s.BACKUP_R2_BUCKET == ""
    assert s.BACKUP_ENCRYPTION_KEY == ""
    assert isinstance(s.BACKUP_RETENTION_DAILY, int) and s.BACKUP_RETENTION_DAILY > 0
    assert isinstance(s.BACKUP_RETENTION_WEEKLY, int) and s.BACKUP_RETENTION_WEEKLY > 0


def test_daily_retention_within_analytics_staleness_ceiling():
    """The dump carries YouTube analytics rows, so dailies must expire within the
    30-day ToS staleness window (COMPLIANCE.md). Weeklies live under a separate
    prefix/lifecycle and may exceed it for the precious non-analytics slice."""
    from config import Settings

    s = Settings()
    assert s.BACKUP_RETENTION_DAILY <= 30, (
        f"BACKUP_RETENTION_DAILY must stay <=30 days for the analytics rows the "
        f"dump carries (YouTube ToS staleness). Got {s.BACKUP_RETENTION_DAILY}."
    )


def test_backup_config_is_not_a_production_boot_requirement():
    """_require_prod_secrets must NOT depend on the BACKUP_* settings — the live
    app/worker must not fail to boot over a cron-only backup setting (Issue 256
    decoupling; DECISIONS 2026-06-27)."""
    import config

    src = inspect.getsource(config.Settings._require_prod_secrets)
    assert "BACKUP_" not in src, (
        "Backup config is intentionally decoupled from app-boot validation; "
        "backup_pg.sh validates its own env at runtime. Do not add BACKUP_* to "
        "_require_prod_secrets — it would crash-loop the app over a cron setting."
    )
