"""
Tests for Issue 205 — Stripe ledger reconciliation Beat task.

Unit-level: verifies the grant logic with recorded fixtures (no live Stripe in CI).
Real-Postgres idempotency (UNIQUE(stripe_session_id) SAVEPOINT race) is covered
by test_billing_grant_idempotency_integration.py on staging with a live DB.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Fixtures (recorded Stripe API responses) ─────────────────────────────────

CREATOR_ID = str(uuid.uuid4())
PACK_ID = "creator"

# A paid session with valid metadata — the reconcile task should grant minutes.
PAID_SESSION_FIXTURE = {
    "id": "cs_test_reconcile_paid_001",
    "payment_status": "paid",
    "metadata": {"creator_id": CREATOR_ID, "pack_id": PACK_ID},
    "customer": "cus_test_001",
}

# A session with payment_status='unpaid' — should be ignored by list_recent_paid_sessions.
UNPAID_SESSION_FIXTURE = {
    "id": "cs_test_reconcile_unpaid_001",
    "payment_status": "unpaid",
    "metadata": {"creator_id": CREATOR_ID, "pack_id": PACK_ID},
    "customer": "cus_test_001",
}

# A paid session that is already in the ledger — should be skipped (idempotent).
ALREADY_FULFILLED_SESSION_FIXTURE = {
    "id": "cs_test_reconcile_paid_002",
    "payment_status": "paid",
    "metadata": {"creator_id": CREATOR_ID, "pack_id": PACK_ID},
    "customer": "cus_test_001",
}

# ── list_recent_paid_sessions unit tests ─────────────────────────────────────


def _make_stripe_page(sessions: list[dict], has_more: bool = False) -> MagicMock:
    """Build a mock Stripe list page for ``_STRIPE.checkout.sessions.list``."""
    page = MagicMock()
    page.has_more = has_more
    page.data = sessions
    return page


def test_list_recent_paid_sessions_filters_to_paid_only():
    """Sessions with payment_status != 'paid' must not appear in results."""
    from billing.stripe_client import list_recent_paid_sessions

    page = _make_stripe_page(
        [PAID_SESSION_FIXTURE, UNPAID_SESSION_FIXTURE],
        has_more=False,
    )
    with patch("billing.stripe_client._STRIPE") as mock_stripe:
        mock_stripe.checkout.sessions.list.return_value = page
        results = list_recent_paid_sessions(lookback_hours=48)

    assert len(results) == 1
    assert results[0]["id"] == "cs_test_reconcile_paid_001"
    assert results[0]["payment_status"] == "paid"


def test_list_recent_paid_sessions_paginates_until_no_more():
    """Pagination continues until has_more=False.

    Both sessions use a 'created' timestamp well within the lookback window
    (current time - 1h) so the window-exhaustion guard does not trigger early.
    """
    import time

    from billing.stripe_client import list_recent_paid_sessions

    recent_ts = int(time.time()) - 3600  # 1 hour ago — within any 48h lookback
    session_a = {**PAID_SESSION_FIXTURE, "id": "cs_test_a", "created": recent_ts}
    session_b = {**PAID_SESSION_FIXTURE, "id": "cs_test_b", "created": recent_ts}

    page1 = MagicMock()
    page1.has_more = True
    page1.data = [session_a]

    page2 = MagicMock()
    page2.has_more = False
    page2.data = [session_b]

    with patch("billing.stripe_client._STRIPE") as mock_stripe:
        mock_stripe.checkout.sessions.list.side_effect = [page1, page2]
        results = list_recent_paid_sessions(lookback_hours=48)

    assert len(results) == 2
    # Second call must use starting_after=session_a["id"] (cursor-based pagination)
    second_call_params = mock_stripe.checkout.sessions.list.call_args_list[1][0][0]
    assert second_call_params["starting_after"] == "cs_test_a"


def test_list_recent_paid_sessions_stops_before_lookback_window():
    """If the oldest session on a page predates the lookback window, stop paging."""
    from billing.stripe_client import list_recent_paid_sessions

    # A session with a very old created timestamp (outside any realistic window).
    old_session = {**PAID_SESSION_FIXTURE, "id": "cs_old", "created": 1}

    page1 = MagicMock()
    page1.has_more = True
    page1.data = [old_session]

    with patch("billing.stripe_client._STRIPE") as mock_stripe:
        mock_stripe.checkout.sessions.list.return_value = page1
        list_recent_paid_sessions(lookback_hours=48)

    # Should stop after one page even though has_more=True, because the session
    # predates the cutoff.
    assert mock_stripe.checkout.sessions.list.call_count == 1


# ── _reconcile_stripe_ledger_async unit tests ─────────────────────────────────


@pytest.mark.asyncio
async def test_reconcile_grants_unfulfilled_paid_session():
    """An unfulfilled paid session triggers grant_minutes.

    The async helper uses lazy imports (``from billing.stripe_client import ...``
    inside the function body). Because the import re-binds a local name each call,
    we patch at the module level — ``billing.stripe_client.list_recent_paid_sessions``
    and ``billing.ledger.grant_minutes`` — so the re-import sees the mock.
    """
    from worker.tasks import _reconcile_stripe_ledger_async

    mock_session = AsyncMock()
    mock_session.scalar = AsyncMock(return_value=None)  # no existing MinutePack row
    mock_session.commit = AsyncMock()

    with (
        patch(
            "billing.stripe_client.list_recent_paid_sessions", return_value=[PAID_SESSION_FIXTURE]
        ),
        patch("billing.ledger.grant_minutes", new_callable=AsyncMock) as mock_grant,
        patch("db.AdminSessionLocal") as mock_ctx,
    ):
        mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        await _reconcile_stripe_ledger_async()

    mock_grant.assert_awaited_once()
    call_kwargs = mock_grant.call_args
    assert call_kwargs.kwargs["stripe_session_id"] == "cs_test_reconcile_paid_001"
    assert call_kwargs.kwargs["reason"] == "reconcile"


@pytest.mark.asyncio
async def test_reconcile_skips_already_fulfilled_session():
    """A session already in the ledger is a no-op (idempotent sweep)."""
    from worker.tasks import _reconcile_stripe_ledger_async

    mock_session = AsyncMock()
    existing_id = uuid.uuid4()
    mock_session.scalar = AsyncMock(return_value=existing_id)  # already fulfilled

    with (
        patch(
            "billing.stripe_client.list_recent_paid_sessions",
            return_value=[ALREADY_FULFILLED_SESSION_FIXTURE],
        ),
        patch("billing.ledger.grant_minutes", new_callable=AsyncMock) as mock_grant,
        patch("db.AdminSessionLocal") as mock_ctx,
    ):
        mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        await _reconcile_stripe_ledger_async()

    # grant_minutes must NOT be called for an already-fulfilled session
    mock_grant.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconcile_logs_error_on_missing_metadata():
    """Sessions with missing creator_id or pack_id emit an error log and are skipped."""
    from worker.tasks import _reconcile_stripe_ledger_async

    bad_session = {
        "id": "cs_test_bad_meta",
        "payment_status": "paid",
        "metadata": {},  # missing creator_id + pack_id
        "customer": None,
    }

    mock_session = AsyncMock()
    mock_session.scalar = AsyncMock(return_value=None)

    with (
        patch("billing.stripe_client.list_recent_paid_sessions", return_value=[bad_session]),
        patch("billing.ledger.grant_minutes", new_callable=AsyncMock) as mock_grant,
        patch("db.AdminSessionLocal") as mock_ctx,
        patch("worker.tasks.logger") as mock_logger,
    ):
        mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        await _reconcile_stripe_ledger_async()

    mock_grant.assert_not_awaited()
    # An error must be logged — the PII-free alert
    assert mock_logger.error.called
