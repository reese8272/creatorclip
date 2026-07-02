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
from config import settings

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


def test_estimate_cost_usd_prices_cache_tokens() -> None:
    """Cached tokens must NOT bill at 0× (OFF_COURSE_BUGS 2026-06-24).

    usage.input_tokens is the uncached remainder only; cache reads bill at 0.1×
    the input rate and 5-min-TTL writes at 1.25×.
    """
    # 1M cache READS at $3/MTok × 0.1 = $0.30
    assert abs(_estimate_cost_usd(0, 0, 3.0, 15.0, cache_read_tokens=1_000_000) - 0.30) < 1e-9
    # 1M cache WRITES (default 5-min TTL) at $3/MTok × 1.25 = $3.75
    assert abs(_estimate_cost_usd(0, 0, 3.0, 15.0, cache_creation_tokens=1_000_000) - 3.75) < 1e-9
    # 1h-TTL write premium is 2× → $6.00
    cost_1h = _estimate_cost_usd(
        0, 0, 3.0, 15.0, cache_creation_tokens=1_000_000, cache_write_multiplier=2.0
    )
    assert abs(cost_1h - 6.0) < 1e-9


# ── Unit: extended price-book math (Issue 289) ────────────────────────────────


def test_estimate_cost_deepgram_minutes() -> None:
    """A known Deepgram-minutes figure yields the expected USD amount.

    Formula: minutes * COST_PER_MIN_DEEPGRAM
    60 minutes of Nova-3 pre-recorded audio at $0.0077/min = $0.462 exactly.
    This test is a pure-math assertion — no DB, no network.
    """
    minutes = 60.0
    expected_usd = minutes * settings.COST_PER_MIN_DEEPGRAM  # 60 * 0.0077 = 0.462
    assert abs(expected_usd - 0.462) < 1e-9, (
        f"Expected $0.462 for 60 min at ${settings.COST_PER_MIN_DEEPGRAM}/min, got ${expected_usd}"
    )


def test_estimate_cost_mixed_llm_and_deepgram() -> None:
    """A mixed token+Deepgram-minutes cost calculation against the config constants.

    Simulates a job that uses both LLM tokens (Sonnet) and Deepgram transcription:
    - 500k Sonnet input tokens at $3/MTok  = $1.50
    - 200k Sonnet output tokens at $15/MTok = $3.00
    - 30 min Deepgram Nova-3 at $0.0077/min = $0.231
    Total expected: $4.731
    """
    tokens_in = 500_000
    tokens_out = 200_000
    deepgram_minutes = 30.0

    llm_cost = _estimate_cost_usd(
        tokens_in,
        tokens_out,
        settings.COST_PER_MTOK_IN_SONNET,
        settings.COST_PER_MTOK_OUT_SONNET,
    )
    transcription_cost = deepgram_minutes * settings.COST_PER_MIN_DEEPGRAM
    total_cost = llm_cost + transcription_cost

    expected = 1.50 + 3.00 + 0.231
    assert abs(total_cost - expected) < 1e-9, (
        f"Expected ${expected:.9f} for mixed LLM+Deepgram job, got ${total_cost:.9f}"
    )


def test_price_book_version_is_set() -> None:
    """PRICE_BOOK_VERSION must be a non-empty string (rate-change tracking sentinel)."""
    assert settings.PRICE_BOOK_VERSION, "PRICE_BOOK_VERSION must not be empty"
    assert isinstance(settings.PRICE_BOOK_VERSION, str)


def test_nova3_price_and_version_pinned() -> None:
    """Issue 293 — pin the nova-3 rate and its version stamp.

    Prod transcribes with nova-3 ($0.0077/min, deepgram.com/pricing 2026-07-02);
    the old 0.0043 nova-2 rate under-billed every transcription minute. A silent
    revert of either value must fail loudly here.
    """
    assert settings.COST_PER_MIN_DEEPGRAM == 0.0077
    assert settings.PRICE_BOOK_VERSION == "2026-07-02"


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
    assert call_kwargs.args[4] == 500  # tokens_out
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
