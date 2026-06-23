"""Unit tests for the Usage cost ledger helper (Issue 220).

Tests the DRY upsert helper and cost-estimate math locally without Postgres.
The actual upsert-against-the-unique-constraint + RLS behaviour is
staging-authoritative (real Postgres required per project testing rules).
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from billing.ledger import _estimate_cost_usd, record_llm_usage

# ── Unit: cost estimation math ─────────────────────────────────────────────────


def test_estimate_cost_usd_sonnet_exact() -> None:
    """Confirm per-million-token rate arithmetic is exact."""
    # 1 million input tokens at $3/MTok = $3.00
    cost = _estimate_cost_usd(1_000_000, 0, 3.0, 15.0)
    assert abs(cost - 3.0) < 1e-9


def test_estimate_cost_usd_haiku_exact() -> None:
    # 1 million output tokens at $5/MTok = $5.00
    cost = _estimate_cost_usd(0, 1_000_000, 1.0, 5.0)
    assert abs(cost - 5.0) < 1e-9


def test_estimate_cost_usd_combined() -> None:
    # 500k input at $3/MTok + 100k output at $15/MTok = $1.50 + $1.50 = $3.00
    cost = _estimate_cost_usd(500_000, 100_000, 3.0, 15.0)
    assert abs(cost - 3.0) < 1e-9


def test_estimate_cost_usd_zero_tokens() -> None:
    assert _estimate_cost_usd(0, 0, 3.0, 15.0) == 0.0


# ── Unit: record_llm_usage (mocked DB) ────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_llm_usage_calls_increment() -> None:
    """record_llm_usage opens an AdminSessionLocal and calls increment_usage."""
    creator_id = uuid.uuid4()
    usage = {"input_tokens": 1000, "output_tokens": 500, "cache_read": 0, "cache_creation": 0}

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("billing.ledger.increment_usage", new=AsyncMock()) as mock_inc,
        patch("db.AdminSessionLocal", return_value=mock_session),
    ):
        await record_llm_usage(creator_id, usage, 3.0, 15.0)

    mock_inc.assert_awaited_once()
    call_kwargs = mock_inc.await_args
    # creator_id and token counts must match
    assert call_kwargs.args[1] == creator_id
    assert call_kwargs.args[3] == 1000  # tokens_in
    assert call_kwargs.args[4] == 500   # tokens_out
    # cost_estimate: (1000*3 + 500*15) / 1_000_000 = (3000+7500)/1e6 = 0.0105
    assert abs(call_kwargs.args[5] - 0.0105) < 1e-9


@pytest.mark.asyncio
async def test_record_llm_usage_best_effort_on_error() -> None:
    """record_llm_usage never raises — it swallows DB errors (best-effort)."""
    creator_id = uuid.uuid4()
    usage = {"input_tokens": 100, "output_tokens": 50, "cache_read": 0, "cache_creation": 0}

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("billing.ledger.increment_usage", new=AsyncMock(side_effect=RuntimeError("DB down"))),
        patch("db.AdminSessionLocal", return_value=mock_session),
    ):
        # Must NOT raise — failure is logged and swallowed
        await record_llm_usage(creator_id, usage, 3.0, 15.0)
