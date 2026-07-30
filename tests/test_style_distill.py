"""Tests for style-preference distillation + injection (Issue 371).

Honest test posture: the offline ranking fixture cannot prove LLM influence
(it sorts pre-scored dicts), so the CI-enforced assertions here pin the
MECHANISM — the style block reaches the scorer as a third system block, the
cached DNA block stays byte-identical with or without notes, the brief takes
notes only in the (untrusted-wrapped) user turn, and the distiller task
debounces/bills correctly. A live-lane smoke covers the real call.
"""

import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clip_engine.scoring import score_candidates
from config import settings
from dna.brief import _build_request
from preference.style_distill import build_corpus


def _mock_response(
    text: str = '[{"index": 0, "score": 80, "principle": "Pattern interrupt", "reasoning": "x"}]',
):
    usage = SimpleNamespace(
        input_tokens=100,
        output_tokens=50,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
        cache_creation=None,
    )
    block = SimpleNamespace(type="text", text=text)
    return SimpleNamespace(content=[block], usage=usage, stop_reason="end_turn")


def _candidate() -> dict:
    return {"setup_start_s": 0.0, "peak_s": 10.0, "end_s": 20.0, "start_s": 0.0}


_TIMELINE = {"version": 1, "duration_s": 60.0, "events": []}
# Long enough that static + dna clears the 1024-token cacheable floor.
_LONG_BRIEF = "Creator brief. " * 400


@pytest.mark.asyncio
async def test_style_notes_ride_as_third_block_and_preserve_dna_cache():
    """The cache-preservation pin: block[1] (cached DNA block) must be
    byte-identical with and without style notes — notes only ever land in a
    NEW block after the cache breakpoint."""
    captured: list[list[dict]] = []

    async def _fake_create(**kwargs):
        captured.append(kwargs["system"])
        return _mock_response()

    with patch("clip_engine.scoring._ANTHROPIC") as anthropic:
        anthropic.messages.create = AsyncMock(side_effect=_fake_create)
        await score_candidates([_candidate()], _TIMELINE, dna_brief=_LONG_BRIEF)
        await score_candidates(
            [_candidate()],
            _TIMELINE,
            dna_brief=_LONG_BRIEF,
            style_notes="Prefers fast cuts and cold opens.",
        )

    without_notes, with_notes = captured
    assert len(without_notes) == 2
    assert len(with_notes) == 3
    # Byte-identical DNA block (incl. its cache_control marker) → prefix cache survives.
    assert with_notes[1] == without_notes[1]
    assert "cache_control" in with_notes[1]
    assert "Prefers fast cuts" in with_notes[2]["text"]
    assert "cache_control" not in with_notes[2]
    # Notes must not leak into the earlier (cached) blocks.
    assert "Prefers fast cuts" not in with_notes[0]["text"]
    assert "Prefers fast cuts" not in with_notes[1]["text"]


@pytest.mark.asyncio
async def test_cold_start_ignores_style_notes_no_llm_call():
    with patch("clip_engine.scoring._ANTHROPIC") as anthropic:
        anthropic.messages.create = AsyncMock()
        result = await score_candidates(
            [_candidate()], _TIMELINE, dna_brief=None, style_notes="anything"
        )
    anthropic.messages.create.assert_not_called()
    assert result[0]["principle"] == "Pattern interrupt"


def test_brief_takes_style_notes_only_in_untrusted_user_turn():
    system, messages = _build_request(
        {"long_videos_analyzed": 3}, "My Channel", None, style_notes="Loves dry humor."
    )
    user_content = messages[0]["content"]
    assert '<untrusted name="learned_style_preferences">' in user_content
    assert "Loves dry humor." in user_content
    # Creator-derived text never enters the system role (Issue 224 posture).
    for block in system:
        assert "Loves dry humor." not in block["text"]


def test_build_corpus_shapes_and_caps():
    clip_rows = [{"valence": "keep", "tags": ["good_hook"], "note": "tight editing"}]
    video_rows = [{"valence": "dislike", "tags": ["pacing_off"], "note": None}]
    corpus = build_corpus(clip_rows, video_rows)
    assert "[clip:keep] tags=good_hook note=tight editing" in corpus
    assert "[video:dislike] tags=pacing_off" in corpus

    huge = [{"valence": "keep", "tags": None, "note": "x" * 500} for _ in range(100)]
    assert len(build_corpus(huge, [])) <= 8000


# ── Distiller task (worker) ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_distill_task_skips_when_llm_flag_off():
    from worker.tasks import _distill_style_prefs_async

    with (
        patch("worker.tasks.flag_enabled", new=AsyncMock(return_value=False)),
        patch("worker.tasks.db") as db_mock,
    ):
        await _distill_style_prefs_async(str(uuid.uuid4()))
    db_mock.tenant_session.assert_not_called()


@pytest.mark.asyncio
async def test_distill_task_skips_when_spend_blocked():
    from worker.tasks import _distill_style_prefs_async

    with (
        patch("worker.tasks.flag_enabled", new=AsyncMock(return_value=True)),
        patch("billing.spend_guard.creator_block_status", new=AsyncMock(return_value=(True, 60))),
        patch("worker.tasks.db") as db_mock,
    ):
        await _distill_style_prefs_async(str(uuid.uuid4()))
    db_mock.tenant_session.assert_not_called()


def _session_ctx(session):
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _feedback_row(created_at, *, tags=None, note=None, action="upvote"):
    row = MagicMock()
    row.created_at = created_at
    row.feedback_tags = tags
    row.feedback_note = note
    row.action = SimpleNamespace(value=action)
    row.sentiment = SimpleNamespace(value="like")
    return row


@pytest.mark.asyncio
async def test_distill_task_debounces_below_min_new():
    """Existing watermark + fewer than STYLE_DISTILL_MIN_NEW new rows → no LLM call."""
    from datetime import UTC, datetime, timedelta

    from worker.tasks import _distill_style_prefs_async

    now = datetime.now(UTC)
    existing_notes = MagicMock()
    existing_notes.last_input_at = now - timedelta(hours=1)

    old_row = _feedback_row(now - timedelta(days=2), tags=["good_hook"])
    new_row = _feedback_row(now, note="love it")  # only 1 new (< 3)

    session = AsyncMock()
    results = [
        MagicMock(scalar_one_or_none=MagicMock(return_value=existing_notes)),  # notes row
        MagicMock(scalars=MagicMock(return_value=iter([old_row, new_row]))),  # clip fb
        MagicMock(scalars=MagicMock(return_value=iter([]))),  # video fb
    ]
    session.execute = AsyncMock(side_effect=results)

    with (
        patch("worker.tasks.flag_enabled", new=AsyncMock(return_value=True)),
        patch("billing.spend_guard.creator_block_status", new=AsyncMock(return_value=(False, 0))),
        patch("worker.tasks.db") as db_mock,
        patch("worker.tasks._try_advisory_lock", new=AsyncMock(return_value=True)),
        patch("worker.tasks._rollback_then_unlock", new=AsyncMock()),
        patch("preference.style_distill.distill_style_notes", new=AsyncMock()) as distill_mock,
    ):
        db_mock.tenant_session = MagicMock(return_value=_session_ctx(session))
        await _distill_style_prefs_async(str(uuid.uuid4()))

    distill_mock.assert_not_called()


@pytest.mark.asyncio
async def test_distill_task_happy_path_bills_haiku_and_upserts():
    from datetime import UTC, datetime, timedelta

    from worker.tasks import _distill_style_prefs_async

    now = datetime.now(UTC)
    rows = [
        _feedback_row(now - timedelta(minutes=i), tags=["good_hook"], note=f"n{i}")
        for i in range(4)
    ]

    read_session = AsyncMock()
    read_results = [
        MagicMock(scalar_one_or_none=MagicMock(return_value=None)),  # no notes row yet
        MagicMock(scalars=MagicMock(return_value=iter(rows))),  # clip fb
        MagicMock(scalars=MagicMock(return_value=iter([]))),  # video fb
    ]
    read_session.execute = AsyncMock(side_effect=read_results)

    write_session = AsyncMock()
    write_session.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
    )
    added = []
    write_session.add = MagicMock(side_effect=added.append)

    usage = {"input_tokens": 100, "output_tokens": 50, "cache_read": 0, "cache_creation": 0}
    with (
        patch("worker.tasks.flag_enabled", new=AsyncMock(return_value=True)),
        patch("billing.spend_guard.creator_block_status", new=AsyncMock(return_value=(False, 0))),
        patch("worker.tasks.db") as db_mock,
        patch("worker.tasks._try_advisory_lock", new=AsyncMock(return_value=True)),
        patch("worker.tasks._rollback_then_unlock", new=AsyncMock()),
        patch(
            "preference.style_distill.distill_style_notes",
            new=AsyncMock(return_value=("Wants tighter pacing.", usage)),
        ),
        patch("billing.ledger.record_llm_usage", new=AsyncMock()) as bill_mock,
    ):
        db_mock.tenant_session = MagicMock(
            side_effect=[_session_ctx(read_session), _session_ctx(write_session)]
        )
        await _distill_style_prefs_async(str(uuid.uuid4()))

    # Billed at Haiku rates through the standard choke point.
    bill_mock.assert_awaited_once()
    args = bill_mock.await_args.args
    assert args[2] == settings.COST_PER_MTOK_IN_HAIKU
    assert args[3] == settings.COST_PER_MTOK_OUT_HAIKU
    # Row upserted with the distilled text + watermark.
    assert len(added) == 1
    assert added[0].notes_text == "Wants tighter pacing."
    assert added[0].source_count == 4
    assert added[0].last_input_at == rows[0].created_at


# ── Live lane (deselected by default; RUN_LLM_LIVE=1 + real key) ─────────────


@pytest.mark.llm_live
@pytest.mark.asyncio
async def test_distill_live_smoke():
    if os.environ.get("RUN_LLM_LIVE") != "1":
        pytest.skip("RUN_LLM_LIVE not set")
    from preference.style_distill import distill_style_notes

    clip_rows = [
        {"valence": "keep", "tags": ["good_hook", "editing_matches_pace"], "note": "tight cuts"},
        {"valence": "drop", "tags": ["wrong_length"], "note": "drags after the intro"},
    ]
    video_rows = [
        {"valence": "like", "tags": ["tone_on_brand"], "note": "dry humor works for my audience"}
    ]
    text, usage = await distill_style_notes(clip_rows, video_rows)
    assert text
    assert len(text) <= settings.STYLE_NOTES_MAX_CHARS
    lowered = text.lower()
    assert "viral" not in lowered and "guarantee" not in lowered
    assert usage["output_tokens"] > 0
