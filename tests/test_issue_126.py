"""Issue 126 — Trial UX + billing clarity.

Tests:
  - Structural: Creator.trial_ends_at column wired (nullable, timezone-aware)
  - Structural: migration 0023 adds the column
  - Structural: config carries TRIAL_DURATION_DAYS + LOW_BALANCE_THRESHOLD_MINUTES
  - Structural: BalanceOut carries trial_ends_at / trial_active / trial_days_remaining / low_balance
  - Structural: differentiated 402 copy lives in billing/ledger.py
  - Structural: expire_trials Celery task + Beat schedule registered
  - Behavioral: GET /billing/balance fills trial fields for a creator on day-3 of trial
  - Behavioral: GET /billing/balance reports trial_active=False after trial_ends_at
  - Behavioral: low_balance flag fires when balance < threshold
  - Behavioral: check_positive_balance differentiates 402 copy for trial-ended creator
  - Static: dashboard trial banner element + dismiss button + final-day override
  - Static: nav balance .is-low styling + low-balance-warning utility class in page-shell.css
  - Static: analysis.html carries the low-balance warning surface above the Analyze button
"""

import pathlib
import uuid as _uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

# ── Structural pins ─────────────────────────────────────────────────────────


def test_issue_126_creator_has_trial_ends_at_column_nullable():
    """trial_ends_at must be nullable + timezone-aware; legacy creators keep
    NULL, which the trial-active predicate treats as 'no trial' rather than
    'trial already expired'."""
    from models import Creator

    col = Creator.__table__.columns["trial_ends_at"]
    assert col.nullable is True, "trial_ends_at must be NULL-able for legacy rows"
    # SQLAlchemy DateTime + timezone=True ⇒ Postgres TIMESTAMPTZ
    assert getattr(col.type, "timezone", False), (
        "trial_ends_at must be timezone-aware (Postgres TIMESTAMPTZ)"
    )


def test_issue_126_migration_0023_present_and_chains_after_0022():
    src = (
        pathlib.Path(__file__).parent.parent
        / "alembic"
        / "versions"
        / "0023_creator_trial_ends_at.py"
    ).read_text()
    assert 'down_revision = "0022"' in src, "0023 must chain after 0022"
    assert "trial_ends_at" in src
    # Multi-line add_column form — normalize whitespace before checking.
    normalized = " ".join(src.split())
    assert (
        'add_column( "creators"' in normalized
        or 'add_column("creators"' in normalized
    ), "migration must touch the creators table"


def test_issue_126_config_carries_trial_duration_and_low_balance_threshold():
    from config import settings

    # Defaults match the Phase-1 brief.
    assert settings.TRIAL_DURATION_DAYS >= 1, (
        "TRIAL_DURATION_DAYS must default to a positive integer"
    )
    assert settings.LOW_BALANCE_THRESHOLD_MINUTES >= 1, (
        "LOW_BALANCE_THRESHOLD_MINUTES must default to a positive integer"
    )


def test_issue_126_balance_out_carries_trial_and_low_balance_fields():
    """BalanceOut must expose the four Issue-126 fields; without them the UI
    has no signal for the trial banner / chip color / pre-action warning."""
    from routers.billing import BalanceOut

    fields = set(BalanceOut.model_fields.keys())
    for f in ("trial_ends_at", "trial_active", "trial_days_remaining", "low_balance"):
        assert f in fields, f"BalanceOut must expose {f}"


def test_issue_126_ledger_differentiates_402_copy_when_trial_expired():
    """The user-facing 402 detail copy must differ when the trial is the
    reason the balance is zero — pin the helper that returns it so a future
    'unify error copy' PR can't silently regress the differentiation."""
    from billing.ledger import _trial_ended_402_detail

    msg = _trial_ended_402_detail()
    assert "trial" in msg.lower(), (
        "trial-ended 402 detail must mention 'trial' so the user knows the cause"
    )
    assert "/pricing" in msg, "the 402 detail must point at the pricing CTA"


def test_issue_126_expire_trials_beat_task_registered():
    """The watchdog must be wired to Celery + Beat; without either it'd
    silently never run."""
    from worker import schedule, tasks

    assert hasattr(tasks, "expire_trials"), (
        "worker.tasks must define expire_trials as a Celery task"
    )
    assert "expire-trials-daily" in schedule.celery.conf.beat_schedule, (
        "expire_trials must be on the Beat schedule"
    )
    entry = schedule.celery.conf.beat_schedule["expire-trials-daily"]
    assert entry["task"] == "worker.tasks.expire_trials"


# ── Behavioral: GET /billing/balance ────────────────────────────────────────


def _override_creator(creator):
    from fastapi import Request

    async def _override(request: Request):
        request.state.creator_id = creator.id
        return creator

    return _override


def _mock_balance_session(balance: int):
    from sqlalchemy.ext.asyncio import AsyncSession  # noqa: F401

    mock_session = AsyncMock()
    mock_session.scalar = AsyncMock(return_value=balance)
    return mock_session


def test_issue_126_balance_returns_trial_active_with_days_remaining(client, mocker):
    """A creator 3 days into a 7-day trial should see trial_active=True and
    days_remaining=4 (ceil of 4 days)."""
    from auth import get_current_creator
    from db import get_session
    from main import app

    fake_creator = MagicMock()
    fake_creator.id = _uuid.uuid4()
    fake_creator.trial_ends_at = datetime.now(UTC) + timedelta(days=4)

    mocker.patch("routers.billing.get_balance", AsyncMock(return_value=42))

    mock_session = AsyncMock()

    async def _fake_session():
        yield mock_session

    original = app.dependency_overrides.copy()
    app.dependency_overrides[get_current_creator] = _override_creator(fake_creator)
    app.dependency_overrides[get_session] = _fake_session
    try:
        resp = client.get("/billing/balance")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["minutes_balance"] == 42
        assert body["trial_active"] is True
        assert body["trial_days_remaining"] in (4, 5), body  # ceil tolerance
        assert body["trial_ends_at"] is not None
        # 42 ≥ default LOW_BALANCE_THRESHOLD_MINUTES (10), so low_balance is False.
        assert body["low_balance"] is False
    finally:
        app.dependency_overrides = original


def test_issue_126_balance_reports_expired_trial(client, mocker):
    """When trial_ends_at is in the past, trial_active flips to False."""
    from auth import get_current_creator
    from db import get_session
    from main import app

    fake_creator = MagicMock()
    fake_creator.id = _uuid.uuid4()
    fake_creator.trial_ends_at = datetime.now(UTC) - timedelta(days=2)

    mocker.patch("routers.billing.get_balance", AsyncMock(return_value=0))

    mock_session = AsyncMock()

    async def _fake_session():
        yield mock_session

    original = app.dependency_overrides.copy()
    app.dependency_overrides[get_current_creator] = _override_creator(fake_creator)
    app.dependency_overrides[get_session] = _fake_session
    try:
        resp = client.get("/billing/balance")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["trial_active"] is False
        # 0 minutes is decisively below the threshold.
        assert body["low_balance"] is True
    finally:
        app.dependency_overrides = original


def test_issue_126_balance_reports_no_trial_for_legacy_creator(client, mocker):
    """Legacy creator (NULL trial_ends_at) must not look like 'trial expired';
    they were never on one — fields should report neutral defaults."""
    from auth import get_current_creator
    from db import get_session
    from main import app

    fake_creator = MagicMock()
    fake_creator.id = _uuid.uuid4()
    fake_creator.trial_ends_at = None

    mocker.patch("routers.billing.get_balance", AsyncMock(return_value=500))

    mock_session = AsyncMock()

    async def _fake_session():
        yield mock_session

    original = app.dependency_overrides.copy()
    app.dependency_overrides[get_current_creator] = _override_creator(fake_creator)
    app.dependency_overrides[get_session] = _fake_session
    try:
        resp = client.get("/billing/balance")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["trial_active"] is False
        assert body["trial_ends_at"] is None
        assert body["trial_days_remaining"] is None
        assert body["low_balance"] is False
    finally:
        app.dependency_overrides = original


def test_issue_126_check_positive_balance_uses_trial_copy_when_expired():
    """When the creator's balance is zero AND their trial has expired,
    check_positive_balance must raise with the trial-ended detail copy,
    not the generic 'No minutes remaining' line."""
    import asyncio

    from fastapi import HTTPException

    from billing import ledger

    creator_id = _uuid.uuid4()

    # First scalar() call returns 0 (balance); second returns trial_ends_at in the past.
    mock_session = AsyncMock()
    mock_session.scalar = AsyncMock(
        side_effect=[0, datetime.now(UTC) - timedelta(days=1)]
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(ledger.check_positive_balance(creator_id, mock_session))
    assert exc_info.value.status_code == 402
    assert "trial" in exc_info.value.detail.lower(), (
        "expired-trial 402 must mention 'trial' so the user knows the reason"
    )


def test_issue_126_check_positive_balance_keeps_generic_copy_for_legacy():
    """Same predicate, but trial_ends_at IS NULL (legacy creator): the 402
    must fall back to the generic 'No minutes remaining' copy."""
    import asyncio

    from fastapi import HTTPException

    from billing import ledger

    creator_id = _uuid.uuid4()

    mock_session = AsyncMock()
    mock_session.scalar = AsyncMock(side_effect=[0, None])

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(ledger.check_positive_balance(creator_id, mock_session))
    assert exc_info.value.status_code == 402
    assert "No minutes remaining" in exc_info.value.detail or "Purchase" in exc_info.value.detail


# ── Static: UI surface pins ─────────────────────────────────────────────────


def test_issue_126_dashboard_has_trial_banner_with_dismiss_and_pricing_cta():
    src = (
        pathlib.Path(__file__).parent.parent / "static" / "index.html"
    ).read_text()
    assert 'id="trial-banner"' in src, "dashboard must carry a #trial-banner element"
    assert "renderTrialBanner" in src, "dashboard must define a renderTrialBanner() function"
    assert "dismissTrialBanner" in src, (
        "dashboard must define a dismiss handler so users can dismiss the banner"
    )
    # CTA must link to pricing per Userpilot 2026 guidance (NOT settings).
    assert "/static/pricing.html" in src
    # Final-day override is the load-bearing UX rule — pin the class.
    assert "is-final-day" in src


def test_issue_126_dashboard_has_low_balance_warning_above_actions():
    src = (
        pathlib.Path(__file__).parent.parent / "static" / "index.html"
    ).read_text()
    assert 'id="low-balance-warning"' in src
    assert "renderLowBalanceWarning" in src
    # Must point at pricing as the next step.
    assert 'href="/static/pricing.html">Add minutes</a>' in src or "Add minutes" in src


def test_issue_126_analysis_html_carries_pre_action_low_balance_warning():
    src = (
        pathlib.Path(__file__).parent.parent / "static" / "analysis.html"
    ).read_text()
    assert 'id="low-balance-warning"' in src, (
        "analysis page must surface a low-balance warning above the Analyze button"
    )
    assert "Add minutes" in src


def test_issue_126_auth_js_caches_balance_and_emits_billing_ready():
    src = (
        pathlib.Path(__file__).parent.parent / "static" / "auth.js"
    ).read_text()
    assert "window.__BALANCE__" in src, (
        "auth.js must cache the full balance payload so every page can read it "
        "without re-fetching"
    )
    assert "billing:ready" in src, (
        "auth.js must emit billing:ready so listeners (banner, chip, warnings) "
        "can render synchronously"
    )
    assert "is-low" in src, (
        "auth.js must toggle the .is-low class on #nav-balance for the amber chip"
    )


def test_issue_126_page_shell_css_has_low_chip_and_trial_banner_styles():
    src = (
        pathlib.Path(__file__).parent.parent / "static" / "page-shell.css"
    ).read_text()
    assert ".nav-balance.is-low" in src, (
        "page-shell.css must define the amber low-balance chip state"
    )
    assert ".trial-banner" in src, "page-shell.css must define .trial-banner styling"
    assert ".low-balance-warning" in src, (
        "page-shell.css must define the .low-balance-warning utility class"
    )
