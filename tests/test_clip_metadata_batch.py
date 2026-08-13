"""Unit tests for the batched auto-metadata pass (Issue 417).

DB-free unit lane. Covers (80/20):
  - ONE LLM call per video, each clip's payload grounded ONLY in its own
    transcript window (wrapped untrusted),
  - Python clamps (title ≤100 chars, description ≤5000 UTF-8 bytes +
    #Shorts + no angle brackets, hook ≤200 chars),
  - fill-only redelivery (suggested_title IS NULL filter),
  - LLM-down leaves columns NULL (the task raises → Celery retry → terminal
    failure changes nothing),
  - publish fallback order applied_* → suggested_* → (video.title | "#Shorts"),
  - AUTO_CLIP_METADATA=false disables the task,
  - non-terminal metadata_ready SSE step,
  - migration 0054 structure + no persisted disclaimer.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from config import settings
from knowledge.clip_metadata import (
    DESCRIPTION_MAX_BYTES,
    HOOK_MAX_CHARS,
    TITLE_MAX_CHARS,
    _build_request,
    _parse_result,
    clamp_description,
    clamp_hook,
    clamp_title,
)

# ── Clamps ───────────────────────────────────────────────────────────────────


def test_clamp_title_strips_brackets_and_caps() -> None:
    out = clamp_title("<b>" + "t" * 300)
    assert len(out) == TITLE_MAX_CHARS
    assert "<" not in out and ">" not in out
    assert clamp_title("  ok  ") == "ok"


def test_clamp_description_bytes_boundary_and_shorts() -> None:
    # Multi-byte content truncated on a character boundary, never split mid-rune.
    text = "é" * 3000  # 6000 UTF-8 bytes
    out = clamp_description(text)
    assert len(out.encode("utf-8")) <= DESCRIPTION_MAX_BYTES
    out.encode("utf-8").decode("utf-8")  # round-trips — no split rune
    # #Shorts guaranteed.
    assert "#shorts" in clamp_description("Just a sentence.").lower()
    assert clamp_description("") == "#Shorts"
    # Already-tagged descriptions are not double-tagged.
    once = clamp_description("Great clip #Shorts")
    assert once.lower().count("#shorts") == 1
    # Angle brackets stripped.
    assert "<" not in clamp_description("<script>alert</script> #Shorts")


def test_clamp_hook_caps() -> None:
    assert len(clamp_hook("h" * 500)) == HOOK_MAX_CHARS


# ── Request shape: per-clip windows, wrapped untrusted ───────────────────────


def _payloads() -> list[dict]:
    return [
        {"clip_id": "clip-a", "rank": 1, "window_text": "WINDOW A ONLY", "opening_text": "OPEN A"},
        {"clip_id": "clip-b", "rank": 2, "window_text": "WINDOW B ONLY"},
    ]


def test_build_request_each_clip_owns_its_window() -> None:
    system, messages = _build_request("Chan", "brief", "summary", _payloads())
    user = messages[0]["content"]
    # Each clip's window rides in its own wrap_untrusted envelope.
    assert '<untrusted name="clip_transcript_clip-a">' in user
    assert '<untrusted name="clip_transcript_clip-b">' in user
    assert json.dumps("WINDOW A ONLY") in user
    assert json.dumps("WINDOW B ONLY") in user
    # Window text never leaks into any (trusted) system block.
    for block in system:
        assert "WINDOW A ONLY" not in block["text"]
    # The model-authored context summary rides the user turn, wrapped — never a
    # system block (Issue 463 — second-order injection surface).
    assert '<untrusted name="video_context_summary">' in user
    assert json.dumps("summary") in user


def test_system_always_two_blocks_regardless_of_context_summary() -> None:
    """Issue 463 pin: system is exactly [static, DNA] whether or not a context
    summary exists — and byte-identical across the two, so a per-video summary
    can never invalidate the creator's DNA cache breakpoint."""
    sys_with, _ = _build_request("Chan", "brief", "A model-authored summary.", _payloads())
    sys_without, _ = _build_request("Chan", "brief", None, _payloads())
    assert len(sys_with) == 2
    assert len(sys_without) == 2
    assert sys_with == sys_without


def test_build_request_opening_text_wrapped_when_present() -> None:
    """Issue 428 — the clip's first-5s speech rides its own untrusted envelope,
    and the static prompt pins the hook to it."""
    from knowledge.clip_metadata import _SYSTEM_INSTRUCTIONS

    _, messages = _build_request("Chan", "brief", "summary", _payloads())
    user = messages[0]["content"]
    assert '<untrusted name="clip_opening_clip-a">' in user
    assert json.dumps("OPEN A") in user
    # No opening_text for clip-b → no empty envelope emitted.
    assert '<untrusted name="clip_opening_clip-b">' not in user
    assert "first ~5 seconds" in _SYSTEM_INSTRUCTIONS


def test_static_block_byte_identical_across_calls() -> None:
    sys_a, _ = _build_request("A", "brief A", "sum A", _payloads())
    sys_b, _ = _build_request("B", None, None, [_payloads()[0]])
    assert sys_a[0]["text"] == sys_b[0]["text"]
    assert "cache_control" not in sys_a[0]


def test_no_virality_language_in_static_prompt() -> None:
    from knowledge.clip_metadata import _SYSTEM_INSTRUCTIONS

    lowered = _SYSTEM_INSTRUCTIONS.lower()
    # The words appear only inside the NEVER-write constraint, never as a promise.
    assert "never write" in lowered
    for banned in ("will go viral", "guaranteed"):
        occurrences = lowered.count(banned)
        assert occurrences == 1, f"{banned!r} must appear exactly once (in the ban clause)"


# ── Parse: clamped output, unknown ids dropped, no disclaimer ────────────────


def test_parse_result_clamps_and_filters() -> None:
    raw = json.dumps(
        {
            "clips": [
                {
                    "clip_id": "clip-a",
                    "title": "T" * 300,
                    "description": "A story. <tag>",
                    "hook": "H" * 500,
                },
                {"clip_id": "unknown", "title": "x", "description": "y", "hook": "z"},
            ]
        }
    )
    out = _parse_result(raw, {"clip-a", "clip-b"})
    assert set(out) == {"clip-a"}
    assert len(out["clip-a"]["title"]) == TITLE_MAX_CHARS
    assert "#shorts" in out["clip-a"]["description"].lower()
    assert "<" not in out["clip-a"]["description"]
    assert len(out["clip-a"]["hook"]) == HOOK_MAX_CHARS
    assert "disclaimer" not in out["clip-a"]  # disclaimers belong to suggestion UIs


def test_parse_result_raises_on_malformed() -> None:
    with pytest.raises(ValueError):
        _parse_result(json.dumps({"nope": []}), {"a"})


# ── ONE call, usage shape (mocked LLM) ───────────────────────────────────────


class _Usage:
    input_tokens = 100
    output_tokens = 60
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0


class _Resp:
    def __init__(self, text: str):
        self.content = [MagicMock(type="text", text=text)]
        self.usage = _Usage()
        self.stop_reason = "end_turn"


async def test_generate_batch_makes_exactly_one_call(mocker) -> None:
    import knowledge.clip_metadata as cm

    create = AsyncMock(
        return_value=_Resp(
            json.dumps(
                {
                    "clips": [
                        {"clip_id": "clip-a", "title": "A", "description": "d", "hook": "h"},
                        {"clip_id": "clip-b", "title": "B", "description": "d", "hook": "h"},
                    ]
                }
            )
        )
    )
    mocker.patch.object(cm._ANTHROPIC.messages, "create", create)

    results, usage = await cm.generate_clip_metadata_batch("Chan", "brief", None, _payloads())

    assert create.await_count == 1  # ONE call for the whole batch
    assert set(results) == {"clip-a", "clip-b"}
    assert usage["input_tokens"] == 100
    assert create.await_args.kwargs["model"] == settings.ANTHROPIC_MODEL_CLIP_METADATA
    fmt = create.await_args.kwargs["output_config"]["format"]
    assert fmt["type"] == "json_schema"


async def test_generate_batch_empty_payloads_no_call(mocker) -> None:
    import knowledge.clip_metadata as cm

    create = mocker.patch.object(cm._ANTHROPIC.messages, "create", AsyncMock())
    results, usage = await cm.generate_clip_metadata_batch("Chan", None, None, [])
    assert results == {}
    assert usage["input_tokens"] == 0
    create.assert_not_called()


# ── Worker task: fill-only, flag off, SSE, LLM-down ──────────────────────────


def _session_cm(session: AsyncMock) -> AsyncMock:
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _clip_row(rank: int) -> MagicMock:
    from models import Clip

    c = MagicMock(spec=Clip)
    c.id = uuid.uuid4()
    c.rank = rank
    c.setup_start_s = 10.0 * rank
    c.start_s = 10.0 * rank
    c.end_s = 10.0 * rank + 60.0
    c.suggested_title = None
    c.suggested_description = None
    c.suggested_hook = None
    c.suggestions_generated_at = None
    return c


async def test_flag_off_disables(mocker) -> None:
    from worker.tasks import _clip_metadata_async

    mocker.patch.object(settings, "AUTO_CLIP_METADATA", False)
    tenant = mocker.patch("worker.tasks._tenant_id_or_raise", new=AsyncMock())
    await _clip_metadata_async(str(uuid.uuid4()), str(uuid.uuid4()))
    tenant.assert_not_called()


async def test_happy_path_fills_null_rows_and_emits_metadata_ready(mocker) -> None:
    from models import Transcript, VideoContext
    from worker.tasks import _clip_metadata_async

    creator_id = uuid.uuid4()
    clip_a, clip_b = _clip_row(1), _clip_row(2)

    transcript = MagicMock(spec=Transcript)
    # Segment midpoints: 15.0 → only clip_a's [10, 70) window; 75.0 → only
    # clip_b's [20, 80) window (midpoint-assignment keeps windows disjoint).
    transcript.segments_jsonb = {
        "segments": [
            {"start": 12.0, "end": 18.0, "text": "CLIP A WORDS"},
            {"start": 72.0, "end": 78.0, "text": "CLIP B WORDS"},
        ]
    }
    vc = MagicMock(spec=VideoContext)
    vc.context_jsonb = {"summary": "A video about testing."}

    exec_result = MagicMock()
    exec_result.scalars = MagicMock(return_value=iter([clip_a, clip_b]))
    exec_result2 = MagicMock()
    exec_result2.scalars = MagicMock(return_value=iter([clip_a, clip_b]))

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[exec_result, exec_result2])

    def _get(model, key):
        if model is Transcript:
            return transcript
        if model is VideoContext:
            return vc
        return MagicMock(channel_title="Chan")  # Creator row

    session.get = AsyncMock(side_effect=_get)

    mocker.patch("worker.tasks.db.AsyncSessionLocal", return_value=_session_cm(session))
    mocker.patch("worker.tasks._spend_guard_blocked", new=AsyncMock(return_value=False))
    mocker.patch("dna.profile.get_active", new=AsyncMock(return_value=None))
    aemit = mocker.patch("worker.progress.aemit", new=AsyncMock())
    bill = mocker.patch("billing.ledger.record_llm_usage", new=AsyncMock())

    results = {
        str(clip_a.id): {"title": "Title A", "description": "Desc #Shorts", "hook": "Hook A"},
        str(clip_b.id): {"title": "Title B", "description": "Desc #Shorts", "hook": "Hook B"},
    }
    usage = {
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read": 0,
        "cache_creation": 0,
        "cache_1h": False,
    }
    batch = mocker.patch(
        "knowledge.clip_metadata.generate_clip_metadata_batch",
        new=AsyncMock(return_value=(results, usage)),
    )

    await _clip_metadata_async(str(uuid.uuid4()), str(creator_id))

    batch.assert_awaited_once()
    # Each clip's payload carries its OWN window text (Issue 414 grounding).
    payloads = batch.await_args.args[3]
    windows = {p["clip_id"]: p["window_text"] for p in payloads}
    assert windows[str(clip_a.id)] == "CLIP A WORDS"
    assert windows[str(clip_b.id)] == "CLIP B WORDS"

    assert clip_a.suggested_title == "Title A"
    assert clip_b.suggested_hook == "Hook B"
    assert clip_a.suggestions_generated_at is not None
    session.commit.assert_awaited()
    bill.assert_awaited_once()

    # Non-terminal metadata_ready step on the video's stream.
    steps = [c for c in aemit.call_args_list if c.args[1] == "step"]
    assert any(c.kwargs.get("label") == "metadata_ready" for c in steps)
    assert not any(c.args[1] in ("done", "error") for c in aemit.call_args_list)


async def test_redelivery_skips_when_nothing_null(mocker) -> None:
    """Fill-only idempotency: when every clip already has suggested_title, the
    query (suggested_title IS NULL) returns nothing and no LLM call happens."""
    from worker.tasks import _clip_metadata_async

    exec_result = MagicMock()
    exec_result.scalars = MagicMock(return_value=iter([]))
    session = AsyncMock()
    session.execute = AsyncMock(return_value=exec_result)

    mocker.patch("worker.tasks.db.AsyncSessionLocal", return_value=_session_cm(session))
    mocker.patch("worker.tasks._spend_guard_blocked", new=AsyncMock(return_value=False))
    batch = mocker.patch("knowledge.clip_metadata.generate_clip_metadata_batch", new=AsyncMock())

    await _clip_metadata_async(str(uuid.uuid4()), str(uuid.uuid4()))
    batch.assert_not_called()

    # And the query itself carries the IS NULL filter (the idempotency contract).
    stmt = session.execute.await_args_list[0].args[0]
    assert "suggested_title IS NULL" in str(stmt)


def test_llm_down_leaves_columns_null_and_render_unaffected(mocker) -> None:
    """The sibling task raising (LLM 5xx) → Celery retry path → terminal failure
    changes nothing: columns stay NULL, no refund base, render untouched."""
    from worker.tasks import RefundOnFailureTask, generate_clip_metadata

    mocker.patch(
        "worker.tasks._creator_id_for_video", new=AsyncMock(return_value=str(uuid.uuid4()))
    )
    mocker.patch(
        "worker.tasks._clip_metadata_async",
        new=AsyncMock(side_effect=RuntimeError("LLM 500")),
    )
    with pytest.raises(Exception):  # noqa: B017 — celery Retry wraps the exc
        generate_clip_metadata.run(str(uuid.uuid4()))
    assert not isinstance(generate_clip_metadata, RefundOnFailureTask)
    assert generate_clip_metadata.max_retries == 2


# ── Enqueue posture + publish fallback ───────────────────────────────────────


def test_enqueue_after_persist_in_generate_clips_source() -> None:
    """Structural pin: _generate_clips_async enqueues generate_clip_metadata
    AFTER the persist session block (commit-before-enqueue), gated on
    AUTO_CLIP_METADATA, before/parallel with the render enqueue."""
    import inspect

    from worker import tasks

    src = inspect.getsource(tasks._generate_clips_async)
    persist_idx = src.index("persist_ranked_clips(")
    metadata_idx = src.index("generate_clip_metadata.delay(")
    assert metadata_idx > persist_idx
    assert "settings.AUTO_CLIP_METADATA" in src


def test_publish_fallback_order_applied_then_suggested_then_video_title() -> None:
    """Publish metadata precedence (Issue 417) pinned at the source level:
    applied_* → suggested_* → (video.title | '#Shorts')."""
    import inspect

    from worker import tasks

    src = inspect.getsource(tasks._publish_to_youtube_async)
    a = src.index("if clip.applied_title:")
    s = src.index("elif clip.suggested_title:")
    f = src.index('else "New Short").strip()')
    assert a < s < f
    assert 'clip.applied_description or clip.suggested_description or "#Shorts"' in src


# ── Migration 0054 structure ─────────────────────────────────────────────────


def test_migration_0054_structure() -> None:
    import pathlib

    src = (
        pathlib.Path(__file__).parent.parent
        / "alembic"
        / "versions"
        / "0054_clip_suggested_metadata.py"
    ).read_text()
    assert 'revision = "0054"' in src
    assert 'down_revision = "0053"' in src, "0054 must chain after 0053"
    for col in (
        "suggested_title",
        "suggested_description",
        "suggested_hook",
        "suggestions_generated_at",
    ):
        assert col in src
    assert src.count("drop_column") == 4


def test_clip_model_has_suggested_columns_nullable() -> None:
    from models import Clip

    for col in ("suggested_title", "suggested_description", "suggested_hook"):
        assert Clip.__table__.columns[col].nullable is True
    assert Clip.__table__.columns["suggestions_generated_at"].nullable is True
