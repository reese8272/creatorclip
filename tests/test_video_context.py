"""Unit tests for the whole-video context pass (Issue 415).

DB-free unit lane: the Anthropic client, DB session factories, and ledger are
mocked. Covers (80/20):
  - transcript rendering (paragraph markers, over-budget coarsening never drops
    the tail),
  - context validation (principle registry, span geometry, moment cap, clamps),
  - prompt-cache byte-identity + floor-gated 1h marker (test_brief_caching style),
  - builder round-trip with a mocked LLM (structured output params, usage shape),
  - the never-fail task contract (LLM failure → chain continues, context_skipped),
  - idempotency (existing row → no second spend), spend-guard skip, billing,
  - VIDEO_CONTEXT_ENABLED=false short-circuit.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from analysis.video_context import (
    CLIPPING_PRINCIPLES,
    PROMPT_VERSION,
    _build_request,
    render_transcript_paragraphs,
    validate_context,
)
from config import settings

# ── Transcript rendering ──────────────────────────────────────────────────────


def _segments(*specs: tuple[float, float, str]) -> list[dict]:
    return [{"start": s, "end": e, "text": t} for s, e, t in specs]


def test_render_paragraphs_markers_and_grouping() -> None:
    segs = _segments(
        (0.0, 10.0, "First thought."),
        (12.0, 25.0, "Second thought."),
        (31.0, 45.0, "New paragraph."),
        (512.0, 520.0, "Much later."),
    )
    out = render_transcript_paragraphs(segs, max_chars=10_000)
    lines = out.split("\n")
    assert lines[0] == "[0s] First thought. Second thought."
    assert lines[1] == "[30s] New paragraph."
    assert lines[2] == "[510s] Much later."


def test_render_coarsens_over_budget_never_drops_tail() -> None:
    """Over-budget renders are coarsened PER-PARAGRAPH: every paragraph's
    marker survives (full coverage), each body is capped — the tail is never
    truncated away."""
    segs = _segments(*[(i * 30.0, i * 30.0 + 25.0, "word " * 200) for i in range(20)])
    out = render_transcript_paragraphs(segs, max_chars=2_000)
    lines = out.split("\n")
    assert len(lines) == 20  # every paragraph still present
    assert lines[-1].startswith("[570s] ")  # the tail survived
    assert len(out) <= 2_000 + 20 * 12  # capped near budget (marker overhead tolerated)


def test_render_empty_segments() -> None:
    assert render_transcript_paragraphs([], max_chars=1000) == ""
    assert render_transcript_paragraphs([{"start": 0, "end": 1, "text": "  "}]) == ""


# ── Validation ────────────────────────────────────────────────────────────────


def _moment(start: float, end: float, principle: str, confidence: float) -> dict:
    return {
        "start_s": start,
        "end_s": end,
        "reason": "A complete story delivered calmly.",
        "principle": principle,
        "confidence": confidence,
    }


def _payload(moments: list[dict]) -> dict:
    return {
        "summary": "A video.",
        "structure": [{"start_s": 0.0, "end_s": 600.0, "label": "Main"}],
        "narrative_arcs": ["arc one"],
        "tone": "calm",
        "audience_relevance": "Serves the audience.",
        "moments": moments,
    }


def test_validate_drops_unknown_principle() -> None:
    ctx = validate_context(
        _payload(
            [
                _moment(10.0, 70.0, "Going Viral Fast", 0.9),  # not in the 12 → dropped
                _moment(100.0, 160.0, "Front-load value", 0.8),
            ]
        ),
        duration_s=600.0,
    )
    assert len(ctx["moments"]) == 1
    assert ctx["moments"][0]["principle"] == "Front-load value"


def test_validate_drops_bad_span_geometry() -> None:
    ctx = validate_context(
        _payload(
            [
                _moment(10.0, 25.0, "Front-load value", 0.9),  # 15s < 30s floor
                _moment(0.0, 300.0, "Front-load value", 0.9),  # 300s > 120s cap
                _moment(550.0, 900.0, "Front-load value", 0.9),  # clamped to 600 → 50s OK
            ]
        ),
        duration_s=600.0,
    )
    assert len(ctx["moments"]) == 1
    assert ctx["moments"][0] == {
        "start_s": 550.0,
        "end_s": 600.0,  # bounds-clamped to duration
        "reason": "A complete story delivered calmly.",
        "principle": "Front-load value",
        "confidence": 0.9,
    }


def test_validate_caps_moments_by_confidence() -> None:
    # One more moment than the cap (6 since 2026-08-05) — the lowest-confidence
    # one must be the drop.
    n = settings.LLM_CANDIDATES_MAX + 1
    moments = [
        _moment(i * 100.0, i * 100.0 + 60.0, "Front-load value", 0.5 + i * 0.05) for i in range(n)
    ]
    ctx = validate_context(_payload(moments), duration_s=100.0 * n + 100.0)
    assert len(ctx["moments"]) == settings.LLM_CANDIDATES_MAX == 6
    # The lowest-confidence moment (0.5, at start 0) was the one dropped.
    assert all(m["confidence"] > 0.5 for m in ctx["moments"])
    # Survivors are returned chronologically.
    starts = [m["start_s"] for m in ctx["moments"]]
    assert starts == sorted(starts)


def test_validate_clamps_confidence_and_tolerates_garbage() -> None:
    ctx = validate_context(
        _payload(
            [
                _moment(10.0, 70.0, "Front-load value", 7.5),  # clamped to 1.0
                {"start_s": "not-a-number", "end_s": 50, "principle": "Front-load value"},
                "not even a dict",
            ]
        ),
        duration_s=600.0,
    )
    assert len(ctx["moments"]) == 1
    assert ctx["moments"][0]["confidence"] == 1.0
    assert ctx["version"] == PROMPT_VERSION


def test_twelve_principles_registry() -> None:
    """The moment-validation registry is exactly the 12 named principles —
    including #12 Clean Context Boundary (the one the scorer gains in 416)."""
    assert len(CLIPPING_PRINCIPLES) == 12
    assert "Clean Context Boundary" in CLIPPING_PRINCIPLES


# ── Prompt-cache structure (test_brief_caching.py style) ─────────────────────


def test_static_block_byte_identical_across_calls() -> None:
    """Block 1 must be byte-identical regardless of creator/video inputs, or
    the cache marker on block 2 is silently invalidated every call."""
    sys_a, _ = _build_request("transcript A", 100.0, "brief A", "identity A")
    sys_b, _ = _build_request("completely different", 999.0, None, None)
    assert sys_a[0]["text"] == sys_b[0]["text"]
    assert "cache_control" not in sys_a[0]


def test_cache_marker_floor_gated() -> None:
    # Long DNA brief → block1+block2 clears the 1024-token floor → ttl:1h marker.
    long_brief = "d" * 5000
    system, _ = _build_request("t", 100.0, long_brief, None)
    assert len(system) == 2
    assert system[1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}

    # No identity, no brief → no block 2 at all (no volatile placeholder bytes).
    system_none, _ = _build_request("t", 100.0, None, None)
    assert len(system_none) == 1


def test_transcript_is_untrusted_user_turn_only() -> None:
    transcript = "SECRET-MARKER transcript content"
    system, messages = _build_request(transcript, 100.0, "brief", "identity")
    for block in system:
        assert "SECRET-MARKER" not in block["text"]
    user = messages[0]["content"]
    assert '<untrusted name="video_transcript">' in user
    assert json.dumps(transcript) in user  # JSON-encoded, not raw


# ── Builder round-trip (mocked LLM) ──────────────────────────────────────────


class _Block:
    def __init__(self, type_: str, text: str | None = None):
        self.type = type_
        self.text = text


class _Usage:
    input_tokens = 100
    output_tokens = 50
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0


class _Resp:
    def __init__(self, content: list):
        self.content = content
        self.usage = _Usage()
        self.stop_reason = "end_turn"


async def test_build_video_context_round_trip(mocker) -> None:
    import analysis.video_context as vc

    captured: dict = {}

    def _create(**kwargs):
        captured.update(kwargs)
        return _Resp(
            [_Block("text", json.dumps(_payload([_moment(10.0, 70.0, "Front-load value", 0.8)])))]
        )

    mocker.patch.object(vc._ANTHROPIC.messages, "create", AsyncMock(side_effect=_create))

    context, usage = await vc.build_video_context(
        _segments((0.0, 10.0, "Hello world.")), 600.0, "brief", None
    )

    assert captured["model"] == settings.ANTHROPIC_MODEL_VIDEO_CONTEXT
    assert captured["max_tokens"] == 8000  # Opus 5: cap covers thinking + text (2026-08-05)
    fmt = captured["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"]["additionalProperties"] is False

    assert context["version"] == PROMPT_VERSION
    assert len(context["moments"]) == 1
    assert usage["input_tokens"] == 100
    assert usage["cache_1h"] is False  # short prefix → floor gate kept the marker off


async def test_build_video_context_raises_on_non_json(mocker) -> None:
    import analysis.video_context as vc

    mocker.patch.object(
        vc._ANTHROPIC.messages,
        "create",
        AsyncMock(return_value=_Resp([_Block("text", "I could not analyze this video.")])),
    )
    with pytest.raises(json.JSONDecodeError):
        await vc.build_video_context(_segments((0.0, 10.0, "Hi.")), 600.0, None, None)


async def test_build_video_context_raises_on_empty_transcript() -> None:
    with pytest.raises(ValueError):
        await __import__("analysis.video_context", fromlist=["x"]).build_video_context(
            [], 600.0, None, None
        )


# ── Task contract: the chain member can never fail ───────────────────────────


def test_task_returns_video_id_on_any_failure() -> None:
    """Mocked LLM/impl failure → the task logs, emits context_skipped, and
    RETURNS video_id so the chain continues to build_signals (signal-only)."""
    from worker.tasks import analyze_video_context

    video_id = str(uuid.uuid4())
    emitted: list[dict] = []

    async def _fake_aemit(task_id, event_type, **fields):
        emitted.append({"task_id": task_id, "type": event_type, **fields})

    with (
        patch("worker.tasks._creator_id_for_video", new=AsyncMock(return_value=str(uuid.uuid4()))),
        patch(
            "worker.tasks._video_context_async",
            new=AsyncMock(side_effect=RuntimeError("LLM 500")),
        ),
        patch("worker.progress.aemit", new=AsyncMock(side_effect=_fake_aemit)),
    ):
        result = analyze_video_context.run(video_id)

    assert result == video_id  # chain continues
    skipped = [e for e in emitted if e.get("label") == "context_skipped"]
    assert len(skipped) == 1
    assert skipped[0]["stage"] == "video_context"


def test_task_has_no_retries_and_plain_base() -> None:
    """max_retries=0, and NOT RefundOnFailureTask — a broken context pass must
    never refund a healthy pipeline or re-deliver."""
    from worker.tasks import RefundOnFailureTask, analyze_video_context

    assert analyze_video_context.max_retries == 0
    assert not isinstance(analyze_video_context, RefundOnFailureTask)


# ── Async impl: flag off / idempotency / spend guard / billing ───────────────


def _session_cm(session: AsyncMock) -> AsyncMock:
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


async def test_disabled_flag_short_circuits(mocker) -> None:
    """VIDEO_CONTEXT_ENABLED=false → today's pipeline exactly: no tenant
    resolution, no DB, no LLM, no events."""
    from worker.tasks import _video_context_async

    mocker.patch.object(settings, "VIDEO_CONTEXT_ENABLED", False)
    tenant = mocker.patch("worker.tasks._tenant_id_or_raise", new=AsyncMock())
    aemit = mocker.patch("worker.progress.aemit", new=AsyncMock())

    await _video_context_async(str(uuid.uuid4()), str(uuid.uuid4()))

    tenant.assert_not_called()
    aemit.assert_not_called()


async def test_existing_row_is_noop_no_double_spend(mocker) -> None:
    """PK check-then-insert idempotency: an existing VideoContext row means a
    redelivery never calls the LLM again."""
    from worker.tasks import _video_context_async

    creator_id = str(uuid.uuid4())
    session = AsyncMock()
    session.get = AsyncMock(return_value=MagicMock())  # first get → existing row

    mocker.patch("worker.tasks.db.AsyncSessionLocal", return_value=_session_cm(session))
    mocker.patch("worker.tasks._spend_guard_blocked", new=AsyncMock(return_value=False))
    mocker.patch("worker.progress.aemit", new=AsyncMock())
    build = mocker.patch("analysis.video_context.build_video_context", new=AsyncMock())

    await _video_context_async(str(uuid.uuid4()), creator_id)

    build.assert_not_called()


async def test_spend_guard_blocked_skips_with_event(mocker) -> None:
    from worker.tasks import _video_context_async

    aemit = mocker.patch("worker.progress.aemit", new=AsyncMock())
    mocker.patch("worker.tasks._spend_guard_blocked", new=AsyncMock(return_value=True))
    build = mocker.patch("analysis.video_context.build_video_context", new=AsyncMock())
    factory = mocker.patch("worker.tasks.db.AsyncSessionLocal")

    await _video_context_async(str(uuid.uuid4()), str(uuid.uuid4()))

    build.assert_not_called()
    factory.assert_not_called()  # skipped before any DB session
    labels = [c.kwargs.get("label") for c in aemit.call_args_list]
    assert "context_skipped" in labels
    reasons = [c.kwargs.get("reason") for c in aemit.call_args_list]
    assert "spend_guard" in reasons


async def test_happy_path_persists_and_bills_after_roundtrip(mocker) -> None:
    """Full happy path: row persisted with the validated payload + model +
    prompt_version, and the ledger is billed via record_llm_usage AFTER the
    LLM round-trip with the floor-gated 2x multiplier flag."""
    from models import Transcript, Video, VideoContext
    from worker.tasks import _video_context_async

    creator_uuid = uuid.uuid4()
    video_uuid = uuid.uuid4()

    video = MagicMock(spec=Video)
    video.creator_id = creator_uuid
    video.duration_s = 600.0

    transcript = MagicMock(spec=Transcript)
    transcript.segments_jsonb = {"segments": [{"start": 0.0, "end": 10.0, "text": "Hello."}]}

    def _get(model, key):
        if model is VideoContext:
            return None
        if model is Video:
            return video
        if model is Transcript:
            return transcript
        return None

    session = AsyncMock()
    session.get = AsyncMock(side_effect=_get)
    added: list = []
    session.add = MagicMock(side_effect=added.append)

    context = {"version": PROMPT_VERSION, "moments": [], "summary": "s"}
    usage = {
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read": 0,
        "cache_creation": 2000,
        "cache_1h": True,
    }

    mocker.patch("worker.tasks.db.AsyncSessionLocal", return_value=_session_cm(session))
    mocker.patch("worker.tasks._spend_guard_blocked", new=AsyncMock(return_value=False))
    mocker.patch("worker.progress.aemit", new=AsyncMock())
    mocker.patch("dna.profile.get_active", new=AsyncMock(return_value=None))
    mocker.patch("dna.identity.get_current", new=AsyncMock(return_value=None))
    build = mocker.patch(
        "analysis.video_context.build_video_context",
        new=AsyncMock(return_value=(context, usage)),
    )
    bill = mocker.patch("billing.ledger.record_llm_usage", new=AsyncMock())

    await _video_context_async(str(video_uuid), str(creator_uuid))

    build.assert_awaited_once()
    # Billed after the round-trip, with the 1h-write multiplier because cache_1h.
    bill.assert_awaited_once()
    assert bill.await_args.kwargs["cache_write_multiplier"] == 2.0
    assert bill.await_args.args[0] == creator_uuid
    # Row persisted with the validated payload.
    assert len(added) == 1
    row = added[0]
    assert row.video_id == video_uuid
    assert row.context_jsonb == context
    assert row.model == settings.ANTHROPIC_MODEL_VIDEO_CONTEXT
    assert row.prompt_version == PROMPT_VERSION
    session.commit.assert_awaited()


async def test_no_transcript_skips_without_llm(mocker) -> None:
    from models import Transcript, Video, VideoContext
    from worker.tasks import _video_context_async

    video = MagicMock(spec=Video)
    video.creator_id = uuid.uuid4()
    video.duration_s = 600.0

    def _get(model, key):
        if model is VideoContext:
            return None
        if model is Video:
            return video
        if model is Transcript:
            return None
        return None

    session = AsyncMock()
    session.get = AsyncMock(side_effect=_get)

    mocker.patch("worker.tasks.db.AsyncSessionLocal", return_value=_session_cm(session))
    mocker.patch("worker.tasks._spend_guard_blocked", new=AsyncMock(return_value=False))
    aemit = mocker.patch("worker.progress.aemit", new=AsyncMock())
    mocker.patch("dna.profile.get_active", new=AsyncMock(return_value=None))
    mocker.patch("dna.identity.get_current", new=AsyncMock(return_value=None))
    build = mocker.patch("analysis.video_context.build_video_context", new=AsyncMock())

    await _video_context_async(str(uuid.uuid4()), str(uuid.uuid4()))

    build.assert_not_called()
    reasons = [c.kwargs.get("reason") for c in aemit.call_args_list]
    assert "no_transcript" in reasons


# ── Migration 0053 (structural smoke — the test_issue_125 house pattern; real
#    up/down runs live in the integration lane) ──────────────────────────────


def test_migration_0053_structure() -> None:
    import pathlib

    src = (
        pathlib.Path(__file__).parent.parent / "alembic" / "versions" / "0053_video_context.py"
    ).read_text()
    assert 'revision = "0053"' in src
    assert 'down_revision = "0052"' in src, "0053 must chain after head 0052"
    assert "video_context" in src
    # The 0044 RLS pattern: GRANT + ENABLE/FORCE RLS + tenant_isolation policy
    # reaching tenant via videos.creator_id, with the 0045-hardened GUC.
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON" in src
    assert "ENABLE ROW LEVEL SECURITY" in src
    assert "FORCE ROW LEVEL SECURITY" in src
    assert "CREATE POLICY tenant_isolation" in src
    assert "NULLIF(current_setting('app.creator_id', true), '')::uuid" in src
    # Downgrade is a true inverse: policy dropped, RLS disabled, table dropped.
    assert "DROP POLICY IF EXISTS tenant_isolation" in src
    assert "drop_table" in src


def test_video_context_model_shape() -> None:
    """PK = FK to videos (CASCADE) — the 1:1 + idempotency-key contract."""
    from models import VideoContext

    table = VideoContext.__table__
    assert list(table.primary_key.columns.keys()) == ["video_id"]
    fk = next(iter(table.columns["video_id"].foreign_keys))
    assert fk.column.table.name == "videos"
    assert fk.ondelete == "CASCADE"
    assert table.columns["context_jsonb"].nullable is False


# ── Chain shape ──────────────────────────────────────────────────────────────


def test_chain_shape_includes_context_between_transcribe_and_signals() -> None:
    """start_pipeline builds ingest | transcribe | analyze_video_context |
    build_signals (Issue 415 chain contract)."""
    import worker.tasks as wt

    with patch("celery.canvas._chain.apply_async", autospec=True) as apply_async:
        wt.start_pipeline(str(uuid.uuid4()))

    assert apply_async.called
    chain_instance = apply_async.call_args.args[0]  # autospec passes self
    assert [t["task"] for t in chain_instance.tasks] == [
        "worker.tasks.ingest_video",
        "worker.tasks.transcribe_video",
        "worker.tasks.analyze_video_context",
        "worker.tasks.build_signals",
    ]
