"""Wiring tests for Issue 92 — every long-running task emits step + done events.

These are NOT integration tests for the underlying behavior (transcribe quality,
ffmpeg encode, web_search). They pin one thing: the right `aemit(task_id, ...)`
calls fire in the expected stage order, so a future refactor that drops an emit
fails CI loudly instead of silently regressing the UX.

Each test patches:
  - `worker.progress.aemit` with an AsyncMock that records every call
  - The DB session factory + downstream IO so the task runs offline
  - The actual heavy work (ffmpeg, transcribe, claude) with no-ops

The assertion is shape-only: the recorded labels include the expected step
boundaries + terminate with the expected `done` or `error` event type.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests._helpers import override_current_creator


def _emit_labels(mock_emit: AsyncMock) -> list[str]:
    """Extract the `label` (or event type for terminal events) sequence
    from a recorded `aemit` mock."""
    out: list[str] = []
    for call in mock_emit.call_args_list:
        # aemit(task_id, event_type, **fields)
        event_type = call.args[1]
        if event_type in ("done", "error"):
            out.append(event_type)
        else:
            out.append(call.kwargs.get("label") or event_type)
    return out


# ── Upload chain — uses video_id as the SSE stream key ──────────────────────


@pytest.mark.asyncio
async def test_ingest_async_emits_step_sequence_using_video_id(mocker):
    """`_ingest_async(video_id)` emits step events keyed by video_id, in
    stage order: ingest_start → probe_duration → extract_audio → upload_audio
    → deduct_minutes."""
    from worker import tasks

    video_id = str(uuid.uuid4())

    fake_emit = AsyncMock()
    mocker.patch("worker.progress.aemit", fake_emit)

    # Stub the DB sessions — first session opens video → source_uri set;
    # second session is the post-extract update path.
    video_stub = MagicMock()
    video_stub.source_uri = "r2://source/x.mp4"
    video_stub.id = uuid.UUID(video_id)
    video_stub.creator_id = uuid.uuid4()
    video_stub.duration_s = None

    fake_session = AsyncMock()
    fake_session.get = AsyncMock(return_value=video_stub)
    fake_session.commit = AsyncMock()
    fake_session_cm = MagicMock()
    fake_session_cm.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session_cm.__aexit__ = AsyncMock(return_value=None)
    mocker.patch("db.AdminSessionLocal", MagicMock(return_value=fake_session_cm))

    # Stub IO: alocal_path yields a Path-like; ffmpeg + R2 upload are no-ops.
    fake_local_cm = MagicMock()
    fake_local_cm.__aenter__ = AsyncMock(return_value="/tmp/src.mp4")
    fake_local_cm.__aexit__ = AsyncMock(return_value=None)
    mocker.patch("worker.storage.alocal_path", MagicMock(return_value=fake_local_cm))
    mocker.patch(
        "worker.storage.aupload_file",
        AsyncMock(return_value="r2://audio/x.wav"),
    )
    mocker.patch("youtube.ingest.extract_audio_wav", MagicMock())
    mocker.patch("youtube.ingest.probe_duration_s", MagicMock(return_value=300.0))
    mocker.patch("billing.ledger.deduct_for_video", AsyncMock())

    await tasks._ingest_async(video_id)

    # All emits keyed by video_id (not Celery task_id) — pins the deterministic
    # stream-key choice that the frontend depends on.
    for call in fake_emit.call_args_list:
        assert call.args[0] == video_id, (
            f"ingest emits must use video_id as the SSE stream key; got {call.args[0]!r}"
        )

    labels = _emit_labels(fake_emit)
    # The four meaningful stage boundaries — frontend relies on these names.
    assert "ingest_start" in labels
    assert "probe_duration" in labels
    assert "extract_audio" in labels
    assert "upload_audio" in labels
    assert "deduct_minutes" in labels


@pytest.mark.asyncio
async def test_signals_async_emits_non_terminal_ingest_complete(mocker):
    """Wave-3 Fix E: `_signals_async` must emit a NON-terminal
    `step:ingest_complete` (NOT `done`). The terminal `done` now fires
    from `_generate_clips_async`, so the SSE consumer stays subscribed
    through clip generation under the same video_id stream key. Before
    this fix, the UI showed "Ingest complete" while clips were still
    being prepared for 5-30s."""
    from worker import tasks

    video_id = str(uuid.uuid4())

    fake_emit = AsyncMock()
    mocker.patch("worker.progress.aemit", fake_emit)

    video_stub = MagicMock()
    video_stub.id = uuid.UUID(video_id)
    video_stub.source_uri = "r2://audio/x.wav"
    video_stub.ingest_done_at = None
    fake_session = AsyncMock()
    fake_session.get = AsyncMock(return_value=video_stub)
    # signals_async also queries RetentionCurve via .execute(...).scalars().all()
    exec_result = MagicMock()
    scalars_obj = MagicMock()
    scalars_obj.all.return_value = []
    scalars_obj.__iter__ = lambda self: iter([])
    exec_result.scalars.return_value = scalars_obj
    fake_session.execute = AsyncMock(return_value=exec_result)
    fake_session.commit = AsyncMock()
    fake_session.add = MagicMock()
    fake_session_cm = MagicMock()
    fake_session_cm.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session_cm.__aexit__ = AsyncMock(return_value=None)
    mocker.patch("db.AdminSessionLocal", MagicMock(return_value=fake_session_cm))

    fake_local_cm = MagicMock()
    fake_local_cm.__aenter__ = AsyncMock(return_value="/tmp/x.wav")
    fake_local_cm.__aexit__ = AsyncMock(return_value=None)
    mocker.patch("worker.storage.alocal_path", MagicMock(return_value=fake_local_cm))
    mocker.patch("ingestion.audio.extract_audio_events", MagicMock(return_value={}))
    mocker.patch("ingestion.signals.build_signal_timeline", MagicMock(return_value={}))

    await tasks._signals_async(video_id)

    labels = _emit_labels(fake_emit)
    # Wave-3 Fix E: signals_async emits a non-terminal step:ingest_complete.
    # The terminal `done` now fires from _generate_clips_async.
    assert "ingest_complete" in labels, (
        f"signals_async must emit non-terminal step:ingest_complete; got {labels}"
    )
    assert "done" not in labels, (
        f"Wave-3 Fix E: signals_async MUST NOT emit terminal `done` "
        f"(that now fires from _generate_clips_async). Got {labels}"
    )


@pytest.mark.asyncio
async def test_generate_clips_async_emits_terminal_done_on_success(mocker):
    """Wave-3 Fix E: `_generate_clips_async` is the new terminal stage of
    the upload pipeline. Must emit step events (start, score_and_rank)
    and a terminal `done` carrying the clip count."""
    from worker import tasks

    video_id = str(uuid.uuid4())

    fake_emit = AsyncMock()
    mocker.patch("worker.progress.aemit", fake_emit)

    video_stub = MagicMock()
    video_stub.id = uuid.UUID(video_id)
    video_stub.creator_id = uuid.uuid4()

    # Issue 311: _generate_clips_async loads the Creator (for the clips_ready
    # email greeting) between the Video and Signals fetches.
    creator_stub = MagicMock()
    creator_stub.channel_title = "Test Channel"

    signals_stub = MagicMock()
    signals_stub.timeline_jsonb = {}

    fake_session = AsyncMock()
    # get() returns Video, then Creator, then Signals, then Transcript (None).
    fake_session.get = AsyncMock(side_effect=[video_stub, creator_stub, signals_stub, None])
    # `existing_done` check returns None (no rendered clips yet).
    fake_session.scalar = AsyncMock(return_value=None)
    fake_session.commit = AsyncMock()
    fake_session_cm = MagicMock()
    fake_session_cm.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session_cm.__aexit__ = AsyncMock(return_value=None)
    mocker.patch("db.AdminSessionLocal", MagicMock(return_value=fake_session_cm))

    mocker.patch("dna.profile.get_active", AsyncMock(return_value=None))
    fake_clips = []
    for i in range(3):
        c = MagicMock()
        c.id = uuid.uuid4()
        c.rank = i + 1
        c.style_preset = None
        fake_clips.append(c)
    # Issue 82b split: scoring is session-free; persistence runs on a reacquired
    # session. The pre-scoring guard (load_existing_clips) must see no clips.
    mocker.patch("clip_engine.ranking.load_existing_clips", AsyncMock(return_value=[]))
    mocker.patch(
        "clip_engine.ranking.score_and_rank",
        AsyncMock(return_value=[{"rank": i + 1} for i in range(3)]),
    )
    mocker.patch(
        "clip_engine.ranking.persist_ranked_clips",
        AsyncMock(return_value=fake_clips),
    )
    # Auto-render (auto-render): the success path enqueues ONE batch task that
    # renders every clip from a single source download.
    delay_mock = mocker.patch("worker.tasks.render_video_clips.delay")

    await tasks._generate_clips_async(video_id)

    # All emits keyed by video_id — same stream key as the upload chain so
    # the SSE consumer stays subscribed across stages.
    for call in fake_emit.call_args_list:
        assert call.args[0] == video_id, (
            f"generate_clips emits must use video_id as stream key; got {call.args[0]!r}"
        )

    labels = _emit_labels(fake_emit)
    assert "generate_clips_start" in labels
    assert "score_and_rank" in labels
    assert labels[-1] == "done", (
        f"Wave-3 Fix E: generate_clips_async is the new terminal stage; "
        f"last emit must be `done`. Got {labels}"
    )
    done_calls = [c for c in fake_emit.call_args_list if c.args[1] == "done"]
    assert len(done_calls) == 1
    assert done_calls[0].kwargs.get("clip_count") == 3
    # A single batch enqueue carrying (video_id, [all clip ids]).
    delay_mock.assert_called_once()
    assert delay_mock.call_args.args[0] == video_id
    enqueued = set(delay_mock.call_args.args[1])
    assert enqueued == {str(c.id) for c in fake_clips}


def _generate_clips_scaffold(mocker, *, clips, brand_kit_style=None):
    """Shared fakes for the auto-render generate-clips tests.

    Returns (video_id, fake_emit). ``session.scalar`` answers the existing-done
    probe (None) then, if a brand kit is supplied, the CreatorStyle read.
    """
    from config import settings as _settings

    video_id = str(uuid.uuid4())
    fake_emit = AsyncMock()
    mocker.patch("worker.progress.aemit", fake_emit)

    video_stub = MagicMock()
    video_stub.id = uuid.UUID(video_id)
    video_stub.creator_id = uuid.uuid4()
    creator_stub = MagicMock()
    creator_stub.channel_title = "Test Channel"
    signals_stub = MagicMock()
    signals_stub.timeline_jsonb = {}

    fake_session = AsyncMock()
    fake_session.get = AsyncMock(side_effect=[video_stub, creator_stub, signals_stub, None])
    if brand_kit_style is not None:
        style_row = MagicMock()
        style_row.style = brand_kit_style
        fake_session.scalar = AsyncMock(side_effect=[None, style_row])
    else:
        fake_session.scalar = AsyncMock(return_value=None)
    fake_session.commit = AsyncMock()
    fake_session_cm = MagicMock()
    fake_session_cm.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session_cm.__aexit__ = AsyncMock(return_value=None)
    mocker.patch("db.AdminSessionLocal", MagicMock(return_value=fake_session_cm))
    mocker.patch("dna.profile.get_active", AsyncMock(return_value=None))
    # Issue 82b split: session-free scoring + reacquired-session persistence.
    mocker.patch("clip_engine.ranking.load_existing_clips", AsyncMock(return_value=[]))
    mocker.patch(
        "clip_engine.ranking.score_and_rank",
        AsyncMock(return_value=[{"rank": i + 1} for i in range(len(clips))]),
    )
    mocker.patch("clip_engine.ranking.persist_ranked_clips", AsyncMock(return_value=clips))
    # Keep the real defaults discoverable to the tests that override them.
    assert hasattr(_settings, "AUTO_RENDER_CLIPS")
    return video_id, fake_emit


@pytest.mark.asyncio
async def test_generate_clips_async_skips_auto_render_when_disabled(mocker):
    """AUTO_RENDER_CLIPS=False leaves clips pending — no render is enqueued."""
    from config import settings
    from worker import tasks

    mocker.patch.object(settings, "AUTO_RENDER_CLIPS", False)
    clips = [MagicMock(id=uuid.uuid4(), rank=1, style_preset=None)]
    video_id, _ = _generate_clips_scaffold(mocker, clips=clips)
    delay_mock = mocker.patch("worker.tasks.render_video_clips.delay")

    await tasks._generate_clips_async(video_id)

    delay_mock.assert_not_called()


@pytest.mark.asyncio
async def test_generate_clips_async_seeds_brand_kit_and_caps_top_n(mocker):
    """Auto-render seeds each clip's style from the brand kit and honors
    AUTO_RENDER_TOP_N — only the top-N by rank render immediately, and a clip
    that already carries a style_preset is never overwritten."""
    from config import settings
    from worker import tasks

    mocker.patch.object(settings, "AUTO_RENDER_CLIPS", True)
    mocker.patch.object(settings, "AUTO_RENDER_TOP_N", 2)
    kit = {"subtitle": "bold_pop", "captions_enabled": True}

    # Deliberately out of rank order; one clip already has a style.
    c_rank3 = MagicMock(id=uuid.uuid4(), rank=3, style_preset=None)
    c_rank1 = MagicMock(id=uuid.uuid4(), rank=1, style_preset=None)
    c_rank2 = MagicMock(id=uuid.uuid4(), rank=2, style_preset={"subtitle": "minimal"})
    clips = [c_rank3, c_rank1, c_rank2]
    video_id, _ = _generate_clips_scaffold(mocker, clips=clips, brand_kit_style=kit)
    delay_mock = mocker.patch("worker.tasks.render_video_clips.delay")

    await tasks._generate_clips_async(video_id)

    # Top-2 by rank (ranks 1 and 2) enqueue in the single batch; rank-3 does not.
    delay_mock.assert_called_once()
    enqueued = set(delay_mock.call_args.args[1])
    assert enqueued == {str(c_rank1.id), str(c_rank2.id)}
    # rank-1 had no style → seeded from the kit; rank-2 kept its own choice.
    assert c_rank1.style_preset == kit
    assert c_rank2.style_preset == {"subtitle": "minimal"}


@pytest.mark.asyncio
async def test_generate_clips_async_emits_done_on_idempotent_short_circuit(mocker):
    """When clips are already rendered (Issue 46 idempotency guard), the
    function short-circuits — it still must emit a terminal `done` so the
    SSE consumer doesn't hang waiting."""
    from worker import tasks

    video_id = str(uuid.uuid4())

    fake_emit = AsyncMock()
    mocker.patch("worker.progress.aemit", fake_emit)

    video_stub = MagicMock()
    video_stub.id = uuid.UUID(video_id)
    video_stub.creator_id = uuid.uuid4()

    fake_session = AsyncMock()
    fake_session.get = AsyncMock(return_value=video_stub)
    # Returning a Clip.id from the existence check triggers the short-circuit.
    fake_session.scalar = AsyncMock(return_value=uuid.uuid4())
    fake_session.commit = AsyncMock()
    fake_session_cm = MagicMock()
    fake_session_cm.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session_cm.__aexit__ = AsyncMock(return_value=None)
    mocker.patch("db.AdminSessionLocal", MagicMock(return_value=fake_session_cm))

    await tasks._generate_clips_async(video_id)

    labels = _emit_labels(fake_emit)
    assert labels[-1] == "done", (
        f"Idempotent short-circuit must still emit terminal `done` so "
        f"the SSE consumer auto-closes. Got {labels}"
    )


# ── Render — uses clip_id as the SSE stream key ─────────────────────────────


@pytest.mark.asyncio
async def test_render_async_emits_step_sequence_using_clip_id(mocker):
    """`_render_clip_async(clip_id)` emits step + done events keyed by clip_id.
    Per-frame ffmpeg progress is intentionally not parsed — we assert the
    step-level boundaries are present and the terminal `done` fires."""
    from models import RenderStatus
    from worker import tasks

    clip_id = str(uuid.uuid4())

    fake_emit = AsyncMock()
    mocker.patch("worker.progress.aemit", fake_emit)

    clip_stub = MagicMock()
    clip_stub.render_status = RenderStatus.pending
    clip_stub.render_uri = None
    clip_stub.setup_start_s = 5.0
    clip_stub.start_s = 0.0
    clip_stub.end_s = 30.0
    clip_stub.video_id = uuid.uuid4()
    video_stub = MagicMock()
    video_stub.source_uri = "r2://source/x.mp4"

    fake_session = AsyncMock()
    # First session.get returns clip; second returns video; third returns clip again
    # for the final render_uri write.
    fake_session.get = AsyncMock(side_effect=[clip_stub, video_stub, clip_stub])
    fake_session.commit = AsyncMock()
    fake_session_cm = MagicMock()
    fake_session_cm.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session_cm.__aexit__ = AsyncMock(return_value=None)
    mocker.patch("db.AdminSessionLocal", MagicMock(return_value=fake_session_cm))

    fake_local_cm = MagicMock()
    fake_local_cm.__aenter__ = AsyncMock(return_value="/tmp/src.mp4")
    fake_local_cm.__aexit__ = AsyncMock(return_value=None)
    mocker.patch("worker.storage.alocal_path", MagicMock(return_value=fake_local_cm))
    mocker.patch(
        "worker.storage.aupload_file",
        AsyncMock(return_value="r2://clips/x.mp4"),
    )
    mocker.patch("clip_engine.render.render_clip_file", MagicMock())

    await tasks._render_clip_async(clip_id)

    # All emits keyed by clip_id (deterministic stream key for the render UI).
    for call in fake_emit.call_args_list:
        assert call.args[0] == clip_id, f"render emits must use clip_id; got {call.args[0]!r}"

    labels = _emit_labels(fake_emit)
    assert "render_start" in labels
    assert "download_source" in labels
    assert "ffmpeg_encode" in labels
    assert "upload_r2" in labels
    assert labels[-1] == "done", f"render terminal emit must be 'done'; got {labels}"


# ── Catalog sync — Celery task_id passed in by the wrapper ──────────────────


@pytest.mark.asyncio
async def test_sync_channel_catalog_emits_per_video_progress(mocker):
    """`_sync_channel_catalog_async(creator_id, task_id=...)` emits step events
    keyed by the Celery task_id, including a per-video `sync_metrics` step
    carrying i/total so the UI can render `i/N` progress."""
    from worker import tasks

    creator_id = str(uuid.uuid4())
    task_id = "celery-task-abc"

    fake_emit = AsyncMock()
    mocker.patch("worker.progress.aemit", fake_emit)

    creator_stub = MagicMock()
    creator_stub.id = uuid.UUID(creator_id)
    video_a = MagicMock()
    video_a.id = uuid.uuid4()
    video_b = MagicMock()
    video_b.id = uuid.uuid4()

    exec_result = MagicMock()
    scalars_obj = MagicMock()
    scalars_obj.all.return_value = [video_a, video_b]
    exec_result.scalars.return_value = scalars_obj
    # Issue 120: Phase 2 now runs two queries (longs + shorts). Return the 2 videos
    # for the longs query and empty for shorts so the total stays at 2.
    empty_result = MagicMock()
    empty_result.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))

    fake_session = AsyncMock()
    fake_session.get = AsyncMock(return_value=creator_stub)
    # execute call order: advisory lock, longs unmeasured, shorts unmeasured, advisory unlock
    fake_session.execute = AsyncMock(
        side_effect=[exec_result, exec_result, empty_result, MagicMock()]
    )
    fake_session.commit = AsyncMock()
    fake_session_cm = MagicMock()
    fake_session_cm.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session_cm.__aexit__ = AsyncMock(return_value=None)
    mocker.patch("db.AdminSessionLocal", MagicMock(return_value=fake_session_cm))

    mocker.patch(
        "youtube.oauth.get_valid_access_token",
        AsyncMock(return_value="fake-token"),
    )
    mocker.patch("youtube.analytics.sync_video_catalog", AsyncMock())
    mocker.patch("youtube.analytics.sync_video_analytics", AsyncMock())

    await tasks._sync_channel_catalog_async(creator_id, task_id=task_id)

    # All emits keyed by the Celery task_id.
    for call in fake_emit.call_args_list:
        assert call.args[0] == task_id

    labels = _emit_labels(fake_emit)
    assert "fetch_uploads" in labels
    assert "sync_metrics_start" in labels
    # Per-video tick with i/total kwargs.
    sync_metrics_calls = [
        c for c in fake_emit.call_args_list if c.kwargs.get("label") == "sync_metrics"
    ]
    assert len(sync_metrics_calls) == 2, (
        f"expected one sync_metrics emit per unmeasured video; got {len(sync_metrics_calls)}"
    )
    for call in sync_metrics_calls:
        assert "i" in call.kwargs and "total" in call.kwargs
        assert call.kwargs["total"] == 2
    assert labels[-1] == "done"


@pytest.mark.asyncio
async def test_sync_channel_catalog_emits_skip_step_on_per_video_failure(mocker):
    """Wave-3 Fix F: per-video metric failures inside the catalog-sync loop
    must emit a `step:sync_metrics_skipped` event so the SSE consumer's
    `i/total` math stays contiguous. Before this fix, a failed video silently
    disappeared from the stream — `i` jumped from N→N+2 if N+1 errored.

    Class name only on the reason field — never the exception message — to
    preserve the no-PII / no-internal-detail invariant the worker module's
    structural-trust SEV2s depend on.
    """
    from worker import tasks

    creator_id = str(uuid.uuid4())
    task_id = "celery-catalog-fix-f"

    fake_emit = AsyncMock()
    mocker.patch("worker.progress.aemit", fake_emit)

    creator_stub = MagicMock()
    creator_stub.id = uuid.UUID(creator_id)
    # Three unmeasured videos — middle one will raise inside sync_video_analytics.
    video_a = MagicMock()
    video_a.id = uuid.uuid4()
    video_b = MagicMock()
    video_b.id = uuid.uuid4()
    video_c = MagicMock()
    video_c.id = uuid.uuid4()

    exec_result = MagicMock()
    scalars_obj = MagicMock()
    scalars_obj.all.return_value = [video_a, video_b, video_c]
    exec_result.scalars.return_value = scalars_obj
    # Issue 120: Phase 2 now runs two queries (longs + shorts). Return the 3 videos
    # for the longs query and empty for shorts so the total stays at 3.
    empty_result = MagicMock()
    empty_result.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))

    fake_session = AsyncMock()
    fake_session.get = AsyncMock(return_value=creator_stub)
    # execute call order: advisory lock, longs unmeasured, shorts unmeasured, advisory unlock
    fake_session.execute = AsyncMock(
        side_effect=[exec_result, exec_result, empty_result, MagicMock()]
    )
    fake_session.commit = AsyncMock()
    fake_session_cm = MagicMock()
    fake_session_cm.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session_cm.__aexit__ = AsyncMock(return_value=None)
    mocker.patch("db.AdminSessionLocal", MagicMock(return_value=fake_session_cm))

    mocker.patch(
        "youtube.oauth.get_valid_access_token",
        AsyncMock(return_value="fake-token"),
    )
    mocker.patch("youtube.analytics.sync_video_catalog", AsyncMock())

    # Second video raises a TimeoutError; first and third succeed.
    sync_results = [None, TimeoutError("simulated timeout"), None]
    mocker.patch(
        "youtube.analytics.sync_video_analytics",
        AsyncMock(side_effect=sync_results),
    )

    await tasks._sync_channel_catalog_async(creator_id, task_id=task_id)

    # Walk the emits and check the per-video sequence is contiguous despite
    # the middle failure.
    labels = [
        (call.kwargs.get("label"), call.kwargs.get("i"))
        for call in fake_emit.call_args_list
        if call.kwargs.get("label") in ("sync_metrics", "sync_metrics_skipped")
    ]
    # The per-video tick sequence MUST include i=1 (success), i=2 (skipped),
    # i=3 (success) — exactly the contiguous coverage Fix F restores.
    assert labels == [
        ("sync_metrics", 1),
        ("sync_metrics_skipped", 2),
        ("sync_metrics", 3),
    ], f"Wave-3 Fix F: per-video tick must be contiguous; got {labels}"

    # The skip event MUST carry the class name as `reason`, NOT the
    # exception message (which could leak token / DB / internal detail).
    skip_emit = next(
        c for c in fake_emit.call_args_list if c.kwargs.get("label") == "sync_metrics_skipped"
    )
    assert skip_emit.kwargs["reason"] == "TimeoutError"
    assert "simulated timeout" not in str(skip_emit.kwargs), (
        "Wave-3 Fix F: skip event must NOT carry the exception message "
        "(PII / internal-detail risk). Class name only."
    )


@pytest.mark.asyncio
async def test_sync_channel_catalog_silent_when_no_task_id(mocker):
    """When called with `task_id=None` (Beat tasks / tests), emits short-
    circuit — no Redis traffic for callers that don't want a stream."""
    from worker import tasks

    fake_emit = AsyncMock()
    mocker.patch("worker.progress.aemit", fake_emit)

    creator_stub = MagicMock()
    creator_stub.id = uuid.uuid4()
    fake_session = AsyncMock()
    fake_session.get = AsyncMock(return_value=creator_stub)
    exec_result = MagicMock()
    scalars_obj = MagicMock()
    scalars_obj.all.return_value = []
    exec_result.scalars.return_value = scalars_obj
    fake_session.execute = AsyncMock(return_value=exec_result)
    fake_session.commit = AsyncMock()
    fake_session_cm = MagicMock()
    fake_session_cm.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session_cm.__aexit__ = AsyncMock(return_value=None)
    mocker.patch("db.AdminSessionLocal", MagicMock(return_value=fake_session_cm))

    mocker.patch(
        "youtube.oauth.get_valid_access_token",
        AsyncMock(return_value="fake-token"),
    )
    mocker.patch("youtube.analytics.sync_video_catalog", AsyncMock())

    await tasks._sync_channel_catalog_async(str(creator_stub.id), task_id=None)

    fake_emit.assert_not_called()


# ── Routers — stream_url + aset_owner wiring ────────────────────────────────


# ── Wave-5 Fix 1: fail-open aset_owner across all 3 remaining sites ──────────


@pytest.mark.asyncio
async def test_sync_catalog_router_fails_open_on_redis_down(mocker):
    """Wave-5 Fix 1 — sync_catalog: Redis blip during aset_owner MUST NOT 500
    the request. Same fail-open posture as Wave-3 Fix B (improvement brief)
    and Wave-4 Fix 1 (upload)."""
    import redis

    from auth import SESSION_COOKIE, create_session_token, get_current_creator
    from main import app

    creator = MagicMock()
    creator.id = uuid.uuid4()
    # Per-creator cookie so the slowapi rate-limit bucket is unique per test
    # (see docs/OFF_COURSE_BUGS.md 2026-05-31 entry).
    session_cookie = {SESSION_COOKIE: create_session_token(creator.id)}

    fake_task = MagicMock()
    fake_task.id = "celery-sync-redis-down"
    mocker.patch("worker.tasks.sync_channel_catalog.delay", return_value=fake_task)
    aset_owner_mock = mocker.patch(
        "worker.progress.aset_owner",
        new_callable=AsyncMock,
        side_effect=redis.ConnectionError("Redis down"),
    )

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    try:
        from fastapi.testclient import TestClient

        with TestClient(app, raise_server_exceptions=False, cookies=session_cookie) as c:
            resp = c.post("/creators/me/catalog/sync")
    finally:
        app.dependency_overrides.pop(get_current_creator, None)

    assert resp.status_code == 202, (
        f"Wave-5 Fix 1: Redis-down on sync_catalog aset_owner MUST NOT 500. "
        f"Got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert body["task_id"] == "celery-sync-redis-down"
    assert body["stream_url"] is None, (
        "Wave-5 Fix 1: stream_url MUST be None when aset_owner fails."
    )
    aset_owner_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_build_dna_router_fails_open_on_redis_down(mocker):
    """Wave-5 Fix 1 — build_dna: same fail-open shape."""
    import redis

    from auth import SESSION_COOKIE, create_session_token, get_current_creator
    from main import app

    creator = MagicMock()
    creator.id = uuid.uuid4()
    session_cookie = {SESSION_COOKIE: create_session_token(creator.id)}

    fake_task = MagicMock()
    fake_task.id = "celery-dna-redis-down"
    delay_mock = mocker.patch(
        "worker.tasks.build_dna.delay",
        return_value=fake_task,
    )
    aset_owner_mock = mocker.patch(
        "worker.progress.aset_owner",
        new_callable=AsyncMock,
        side_effect=redis.ConnectionError("Redis down"),
    )

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    try:
        from fastapi.testclient import TestClient

        with TestClient(app, raise_server_exceptions=False, cookies=session_cookie) as c:
            resp = c.post("/creators/me/dna/build")
    finally:
        app.dependency_overrides.pop(get_current_creator, None)

    assert resp.status_code == 202, (
        f"Wave-5 Fix 1: Redis-down on build_dna aset_owner MUST NOT 500. "
        f"Got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert body["task_id"] == "celery-dna-redis-down"
    assert body["stream_url"] is None
    # The Celery task was still enqueued — the work proceeds even without SSE.
    delay_mock.assert_called_once_with(str(creator.id))
    aset_owner_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_render_router_fails_open_on_redis_down(mocker):
    """Wave-5 Fix 1 — render_clip: same fail-open shape. aset_owner uses
    clip_id (NOT task.id) as the stream key (Issue 92 design); fail-open
    must use the SAME key for consistency."""
    import redis

    from auth import SESSION_COOKIE, create_session_token, get_current_creator
    from db import get_session
    from main import app
    from models import Clip, RenderStatus

    creator = MagicMock()
    creator.id = uuid.uuid4()
    clip_id = uuid.uuid4()
    session_cookie = {SESSION_COOKIE: create_session_token(creator.id)}

    clip_stub = MagicMock(spec=Clip)
    clip_stub.creator_id = creator.id
    clip_stub.render_status = RenderStatus.pending

    fake_session = AsyncMock()
    fake_session.scalar = AsyncMock(return_value=100)  # balance check passes
    fake_session.get = AsyncMock(return_value=clip_stub)

    async def _fake_session_gen():
        yield fake_session

    fake_task = MagicMock()
    fake_task.id = "celery-render-redis-down"
    delay_mock = mocker.patch("worker.tasks.render_clip.delay", return_value=fake_task)
    aset_owner_mock = mocker.patch(
        "worker.progress.aset_owner",
        new_callable=AsyncMock,
        side_effect=redis.ConnectionError("Redis down"),
    )

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = _fake_session_gen
    try:
        from fastapi.testclient import TestClient

        with TestClient(app, raise_server_exceptions=False, cookies=session_cookie) as c:
            resp = c.post(f"/clips/{clip_id}/render")
    finally:
        app.dependency_overrides.pop(get_current_creator, None)
        app.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 202, (
        f"Wave-5 Fix 1: Redis-down on render aset_owner MUST NOT 500. "
        f"Got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert body["task_id"] == "celery-render-redis-down"
    assert body["stream_url"] is None
    delay_mock.assert_called_once()
    # Critically: aset_owner was called with clip_id (NOT task.id) — pins the
    # Issue-92 deterministic-stream-key invariant even on the failure path.
    aset_owner_mock.assert_awaited_once_with(str(clip_id), str(creator.id))


@pytest.mark.asyncio
async def test_sync_catalog_router_stamps_owner_and_returns_stream_url(mocker):
    """POST /me/catalog/sync must stamp ownership on the Celery task_id and
    return stream_url. Pins the wiring from router → progress.aset_owner."""
    from auth import get_current_creator
    from main import app

    creator = MagicMock()
    creator.id = uuid.uuid4()

    fake_task = MagicMock()
    fake_task.id = "celery-task-xyz"
    delay_mock = mocker.patch(
        "worker.tasks.sync_channel_catalog.delay",
        return_value=fake_task,
    )
    aset_owner_mock = mocker.patch(
        "worker.progress.aset_owner",
        new_callable=AsyncMock,
    )

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    try:
        from fastapi.testclient import TestClient

        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.post("/creators/me/catalog/sync")
    finally:
        app.dependency_overrides.pop(get_current_creator, None)

    assert resp.status_code == 202
    body = resp.json()
    assert body["task_id"] == "celery-task-xyz"
    assert body["stream_url"] == "/tasks/celery-task-xyz/events"
    delay_mock.assert_called_once()
    aset_owner_mock.assert_awaited_once_with("celery-task-xyz", str(creator.id))


@pytest.mark.asyncio
async def test_render_router_uses_clip_id_as_stream_key(mocker):
    """POST /clips/{clip_id}/render must stamp ownership using clip_id (NOT
    the Celery task_id) so the worker's clip_id-keyed emits are reachable
    by the SSE consumer."""
    from auth import get_current_creator
    from db import get_session
    from main import app
    from models import Clip, RenderStatus

    creator = MagicMock()
    creator.id = uuid.uuid4()
    clip_id = uuid.uuid4()

    clip_stub = MagicMock(spec=Clip)
    clip_stub.creator_id = creator.id
    clip_stub.render_status = RenderStatus.pending

    fake_session = AsyncMock()
    fake_session.scalar = AsyncMock(return_value=100)  # check_positive_balance passes
    fake_session.get = AsyncMock(return_value=clip_stub)

    async def _fake_session_gen():
        yield fake_session

    fake_task = MagicMock()
    fake_task.id = "celery-render-xyz"
    mocker.patch("worker.tasks.render_clip.delay", return_value=fake_task)
    aset_owner_mock = mocker.patch(
        "worker.progress.aset_owner",
        new_callable=AsyncMock,
    )

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = _fake_session_gen
    try:
        from fastapi.testclient import TestClient

        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.post(f"/clips/{clip_id}/render")
    finally:
        app.dependency_overrides.pop(get_current_creator, None)
        app.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 202
    body = resp.json()
    # task_id is the Celery id (for poll/cancel) but stream_url uses clip_id
    # because the worker emits to task:{clip_id}:events.
    assert body["task_id"] == "celery-render-xyz"
    assert body["stream_url"] == f"/tasks/{clip_id}/events"
    aset_owner_mock.assert_awaited_once_with(str(clip_id), str(creator.id))


def test_upload_response_contract_includes_stream_url():
    """The VideoLinkedOut model must declare stream_url so /videos/upload
    callers see the upload-chain SSE endpoint. Pins the Issue-92 wire shape."""
    from routers.videos import VideoLinkedOut

    fields = VideoLinkedOut.model_fields
    assert "stream_url" in fields, (
        "VideoLinkedOut must include `stream_url` so upload responses surface "
        "the upload-chain SSE endpoint per Issue 92."
    )


# ── Wave-3 Fix D: OAuth callback stamps aset_owner ─────────────────────────


def test_oauth_callback_source_stamps_aset_owner_after_catalog_sync():
    """Wave-3 Fix D (SEV2): the post-OAuth-callback catalog sync at
    routers/auth.py:~117 must stamp progress.aset_owner so the Issue-92
    SSE stream is reachable when Issue 100 wires the onboarding tutorial
    UI. Source-inspect test — the auth callback is integration-tested
    elsewhere; this pins the structural fact that the call is present
    AND wrapped in the fail-open redis.RedisError except clause.
    """
    import inspect

    import routers.auth as auth_module

    src = inspect.getsource(auth_module)
    # The stamp must happen AFTER the .delay() call (mirrors creators.py:167).
    assert "sync_channel_catalog.delay" in src
    assert "progress.aset_owner" in src, (
        "Wave-3 Fix D: routers/auth.py must call progress.aset_owner after "
        "sync_channel_catalog.delay() so the Issue-92 SSE stream key is "
        "authorized for the new creator."
    )
    # The fail-open posture is the second half of the fix — Redis-down must
    # not 500 the OAuth callback.
    assert "RedisError" in src, (
        "Wave-3 Fix D: the aset_owner call must be wrapped in "
        "except redis.RedisError so a Redis blip during onboarding doesn't "
        "500 the OAuth redirect."
    )


# ── Wave-3 Fix B: improvement brief fails open on Redis-down ────────────────


@pytest.mark.asyncio
async def test_improvement_brief_router_returns_stream_url_on_happy_path(mocker):
    """Happy-path: aset_owner succeeds, stream_url is the canonical
    /tasks/{task.id}/events string, the brief is enqueued, the row gets the
    job_id stamped.

    Note: the slowapi limiter keys on creator UUID extracted from the session
    JWT (`limiter._creator_key`). Each test uses a fresh creator UUID so
    rate-limit buckets stay isolated across tests even when Redis is shared.
    """
    from auth import SESSION_COOKIE, create_session_token, get_current_creator
    from db import get_session
    from main import app

    creator = MagicMock()
    creator.id = uuid.uuid4()
    creator.channel_id = "UC_test"
    # Unique-per-test session cookie so the rate limiter keys off this
    # creator's UUID rather than the shared "testclient" IP.
    session_cookie = {SESSION_COOKIE: create_session_token(creator.id)}

    fake_session = AsyncMock()
    # has_metrics check returns a value (existence check passes); fallback
    # re-query of ImprovementBrief returns None (no row at all).
    fake_session.scalar = AsyncMock(side_effect=[uuid.uuid4(), None])
    # Issue 110 Fix C: the new SELECT FOR UPDATE SKIP LOCKED uses
    # session.execute(...).scalar() instead of session.scalar(...) for the
    # debounce-row read. Mock it to return None (no row to lock) so the
    # router falls into the fallback re-query branch.
    no_row_result = MagicMock()
    no_row_result.scalar = MagicMock(return_value=None)
    fake_session.execute = AsyncMock(return_value=no_row_result)
    fake_session.add = MagicMock()
    fake_session.commit = AsyncMock()

    async def _fake_session_gen():
        yield fake_session

    fake_task = MagicMock()
    fake_task.id = "celery-brief-xyz"
    mocker.patch("worker.tasks.generate_improvement_brief.delay", return_value=fake_task)
    aset_owner_mock = mocker.patch("worker.progress.aset_owner", new_callable=AsyncMock)
    # Issue 228: the route now pre-flights a balance floor; this test mocks the
    # session and asserts the enqueue path, not billing — neutralize the gate.
    mocker.patch("routers.improvement.check_positive_balance", AsyncMock(return_value=None))

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = _fake_session_gen
    try:
        from fastapi.testclient import TestClient

        with TestClient(app, raise_server_exceptions=False, cookies=session_cookie) as c:
            resp = c.post("/creators/me/improvement-brief")
    finally:
        app.dependency_overrides.pop(get_current_creator, None)
        app.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 202
    body = resp.json()
    assert body["task_id"] == "celery-brief-xyz"
    assert body["stream_url"] == "/tasks/celery-brief-xyz/events"
    aset_owner_mock.assert_awaited_once_with("celery-brief-xyz", str(creator.id))


@pytest.mark.asyncio
async def test_improvement_brief_router_fails_open_on_redis_down(mocker):
    """Wave-3 Fix B: a Redis blip during aset_owner MUST NOT 500 the request
    or leave the row in an inconsistent state. The brief still gets enqueued,
    the row gets the job_id stamped, and the response returns
    `stream_url=None` so the client can poll the GET endpoint instead.

    Before Fix B, this scenario left the row at `status=pending,
    job_id=None` and 500'd the request — subsequent debounce-collapse calls
    returned `task_id=None, stream_url=None` forever.
    """
    import redis

    from auth import SESSION_COOKIE, create_session_token, get_current_creator
    from db import get_session
    from main import app

    creator = MagicMock()
    creator.id = uuid.uuid4()
    creator.channel_id = "UC_test"
    session_cookie = {SESSION_COOKIE: create_session_token(creator.id)}

    # Track what gets committed so we can assert row state at the end.
    pending_row = MagicMock()
    pending_row.job_id = None
    pending_row.status = None

    fake_session = AsyncMock()
    fake_session.scalar = AsyncMock(side_effect=[uuid.uuid4(), None])
    # Issue 110 Fix C: SELECT FOR UPDATE SKIP LOCKED uses execute(...).scalar()
    no_row_result = MagicMock()
    no_row_result.scalar = MagicMock(return_value=None)
    fake_session.execute = AsyncMock(return_value=no_row_result)
    fake_session.add = MagicMock()
    fake_session.commit = AsyncMock()

    async def _fake_session_gen():
        yield fake_session

    fake_task = MagicMock()
    fake_task.id = "celery-brief-redis-down"
    delay_mock = mocker.patch(
        "worker.tasks.generate_improvement_brief.delay",
        return_value=fake_task,
    )
    aset_owner_mock = mocker.patch(
        "worker.progress.aset_owner",
        new_callable=AsyncMock,
        side_effect=redis.ConnectionError("Redis down"),
    )
    # Issue 228: neutralize the new balance floor — this test asserts the
    # Redis-down fail-open path, not billing.
    mocker.patch("routers.improvement.check_positive_balance", AsyncMock(return_value=None))

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = _fake_session_gen
    try:
        from fastapi.testclient import TestClient

        with TestClient(app, raise_server_exceptions=False, cookies=session_cookie) as c:
            resp = c.post("/creators/me/improvement-brief")
    finally:
        app.dependency_overrides.pop(get_current_creator, None)
        app.dependency_overrides.pop(get_session, None)

    # The request must succeed even though Redis is down.
    assert resp.status_code == 202, (
        f"Wave-3 Fix B: Redis-down on aset_owner MUST NOT 500 the request. "
        f"Got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    # The brief was enqueued before the Redis call, so task_id is real.
    assert body["task_id"] == "celery-brief-redis-down"
    delay_mock.assert_called_once()
    # The aset_owner call was attempted (and raised).
    aset_owner_mock.assert_awaited_once()
    # stream_url is None — the client knows there's no live progress link.
    assert body["stream_url"] is None, (
        "When aset_owner fails, stream_url MUST be None so the client falls "
        "back to GET polling — pinning the fail-open contract."
    )
