"""
Unit tests for Issue 217 — Clip-engine transparency: "what's NOT clipped and why".

Covers:
  (a) derive_skip_reason() returns the correct code for each diagnostic branch
  (b) skip_reason_label() returns a non-empty human-readable string for every code
  (c) Labels contain no virality language (honesty constraint)
  (d) GET /videos/{id}/clips includes skip_reason + skip_reason_label when no clips exist
  (e) Per-creator isolation: skip_reason does not leak cross-creator data
"""

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from auth import get_current_creator
from clip_engine.candidates import (
    SKIP_REASON_ALL_SUPPRESSED,
    SKIP_REASON_NO_RETENTION_DATA,
    SKIP_REASON_NO_SIGNAL,
    SKIP_REASON_SOURCE_UNAVAILABLE,
    derive_skip_reason,
    skip_reason_label,
)
from db import get_session
from main import app
from models import Creator, IngestStatus, Signals, Video
from tests._helpers import owned_lookup_result

# ── Helpers ───────────────────────────────────────────────────────────────────


def _tl(events: list, duration_s: float = 120.0) -> dict:
    return {"duration_s": duration_s, "events": events}


def _retention_spike(t: float) -> dict:
    return {"type": "retention_spike", "start_s": t, "end_s": t + 1.0, "value": 1.5}


def _energy_spike(t: float) -> dict:
    return {"type": "energy_spike", "start_s": t, "end_s": t + 2.0, "value": 0.8}


# ── (a) derive_skip_reason — diagnostic branch coverage ──────────────────────


def test_derive_skip_reason_source_unavailable():
    """No stored source → SKIP_REASON_SOURCE_UNAVAILABLE regardless of timeline."""
    tl = _tl([_retention_spike(60.0)])
    reason = derive_skip_reason(timeline=tl, source_available=False)
    assert reason == SKIP_REASON_SOURCE_UNAVAILABLE


def test_derive_skip_reason_no_signal_empty_timeline():
    """Empty timeline → SKIP_REASON_NO_SIGNAL (no signal array at all)."""
    reason = derive_skip_reason(timeline=_tl([], duration_s=0.0), source_available=True)
    assert reason == SKIP_REASON_NO_SIGNAL


def test_derive_skip_reason_silence_only_no_retention_data():
    """Timeline with only silence events (no retention data) → SKIP_REASON_NO_RETENTION_DATA.

    Silences contribute weight -0.5 to the signal array, which makes the array non-zero
    but produces a trough, not a peak — find_peaks returns no peaks. Since there are also
    no retention_spike events the branch is no_retention_data.
    """
    tl = _tl([{"type": "silence", "start_s": 10.0, "end_s": 20.0}], duration_s=60.0)
    reason = derive_skip_reason(timeline=tl, source_available=True)
    assert reason == SKIP_REASON_NO_RETENTION_DATA


def test_derive_skip_reason_no_retention_data():
    """Timeline has non-zero duration but no events at all → SKIP_REASON_NO_RETENTION_DATA.

    build_signal_array returns a flat-zero array → find_peaks finds no peaks.
    No retention_spike events → reason is no_retention_data (not no_signal, which
    is reserved for empty/zero-duration timelines).
    """
    # Flat timeline: duration > 0 so build_signal_array returns an array, but all
    # values are 0 (no events) → find_peaks finds no peaks. No retention_spike events
    # means no retention data.
    tl = _tl([], duration_s=60.0)
    reason = derive_skip_reason(timeline=tl, source_available=True)
    assert reason == SKIP_REASON_NO_RETENTION_DATA


def test_derive_skip_reason_all_suppressed():
    """Strong peaks exist in the timeline but engine suppressed them (caller context).

    This branch is reached when derive_skip_reason() is called AFTER extract_candidates()
    returned [] for a timeline that DOES have peaks — meaning NMS suppression happened
    or all clips were below MIN_CLIP_S. We simulate it by giving a strong retention spike
    but no silence gap, so all candidates anchor to the same setup start and get merged.
    """
    # Two close retention spikes with retention_spike weight (3.0) — both will fire
    # find_peaks but the resulting candidates overlap heavily.  derive_skip_reason uses
    # find_peaks internally to decide between no_signal and all_suppressed.
    tl = _tl(
        [
            _retention_spike(60.0),
            _retention_spike(65.0),  # 5s apart — within min_distance_samples
        ],
        duration_s=120.0,
    )
    reason = derive_skip_reason(timeline=tl, source_available=True)
    # Peaks exist → reason is not no_signal or no_retention_data
    assert reason == SKIP_REASON_ALL_SUPPRESSED


def test_derive_skip_reason_source_unavailable_beats_signal():
    """source_available=False short-circuits before any timeline inspection."""
    reason = derive_skip_reason(timeline={}, source_available=False)
    assert reason == SKIP_REASON_SOURCE_UNAVAILABLE


# ── (b) skip_reason_label — human-readable strings ───────────────────────────


@pytest.mark.parametrize(
    "reason",
    [
        SKIP_REASON_NO_SIGNAL,
        SKIP_REASON_NO_RETENTION_DATA,
        SKIP_REASON_SOURCE_UNAVAILABLE,
        SKIP_REASON_ALL_SUPPRESSED,
    ],
)
def test_skip_reason_label_non_empty(reason: str):
    """Every known reason code must map to a non-empty label string."""
    label = skip_reason_label(reason)
    assert isinstance(label, str) and len(label.strip()) > 0


def test_skip_reason_label_unknown_code_falls_back():
    """Unknown code returns the code itself (graceful degradation, never empty)."""
    code = "some_future_unknown_code"
    assert skip_reason_label(code) == code


# ── (c) No virality language in any label ────────────────────────────────────

# Positive virality claims are forbidden (CLAUDE.md honesty constraint).
# "not a guarantee" / "no guarantee" are correct honest framing and are NOT banned —
# only banning the positive virality-promise forms.
_VIRALITY_TERMS = frozenset({"viral", "virality", "promises virality", "guaranteed to go viral"})


@pytest.mark.parametrize(
    "reason",
    [
        SKIP_REASON_NO_SIGNAL,
        SKIP_REASON_NO_RETENTION_DATA,
        SKIP_REASON_SOURCE_UNAVAILABLE,
        SKIP_REASON_ALL_SUPPRESSED,
    ],
)
def test_skip_reason_label_no_virality_language(reason: str):
    """Honesty constraint: skip-reason labels must never promise virality."""
    label = skip_reason_label(reason).lower()
    for term in _VIRALITY_TERMS:
        assert term not in label, (
            f"Found virality term {term!r} in skip_reason_label for {reason!r}: {label!r}"
        )


# ── (d) GET /videos/{id}/clips — skip_reason in response ────────────────────


def _creator() -> MagicMock:
    c = MagicMock(spec=Creator)
    c.id = uuid.uuid4()
    return c


def _video(creator_id: uuid.UUID, *, has_source: bool = True) -> MagicMock:
    v = MagicMock(spec=Video)
    v.id = uuid.uuid4()
    v.creator_id = creator_id
    v.ingest_status = IngestStatus.done
    v.source_uri = "r2://some-bucket/source.mp4" if has_source else None
    return v


def _signals(video_id: uuid.UUID, timeline: dict) -> MagicMock:
    s = MagicMock(spec=Signals)
    s.id = video_id
    s.timeline_jsonb = timeline
    return s


def _fake_session(video: MagicMock, clips: list, signals: MagicMock | None) -> Any:
    async def _session():
        session = AsyncMock()

        async def _get(model, pk, **kwargs):
            if model is Signals:
                return signals
            return None

        session.get = AsyncMock(side_effect=_get)

        async def _execute(stmt, *a, **kw):
            entity = stmt.column_descriptions[0]["type"]
            if entity is Video:  # get_owned ownership select (Issue 109e)
                return owned_lookup_result(stmt, video)
            result = MagicMock()
            result.scalars.return_value = iter(clips)
            return result

        session.execute = AsyncMock(side_effect=_execute)
        yield session

    return _session


def _set_overrides(creator, video, clips, signals=None) -> None:
    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _fake_session(video, clips, signals)


def test_list_clips_no_clips_includes_skip_reason_no_signal(client):
    """GET /videos/{id}/clips with no clips and flat signal → skip_reason='no_signal_above_threshold'."""
    creator = _creator()
    video = _video(creator.id)
    tl = {"duration_s": 0.0, "events": []}
    sigs = _signals(video.id, tl)
    _set_overrides(creator, video, [], sigs)

    try:
        with patch("preference.train.load_latest", new=AsyncMock(return_value=None)):
            resp = client.get(f"/videos/{video.id}/clips", cookies={"session": "x"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["skip_reason"] == SKIP_REASON_NO_SIGNAL
    assert body["skip_reason_label"] is not None
    assert len(body["skip_reason_label"]) > 0


def test_list_clips_no_clips_no_source_returns_source_unavailable(client):
    """GET /videos/{id}/clips with no source file → skip_reason='source_unavailable'."""
    creator = _creator()
    video = _video(creator.id, has_source=False)
    sigs = _signals(video.id, {"duration_s": 120.0, "events": []})
    _set_overrides(creator, video, [], sigs)

    try:
        with patch("preference.train.load_latest", new=AsyncMock(return_value=None)):
            resp = client.get(f"/videos/{video.id}/clips", cookies={"session": "x"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["skip_reason"] == SKIP_REASON_SOURCE_UNAVAILABLE


def test_list_clips_no_clips_no_signals_row_returns_no_signal(client):
    """GET /videos/{id}/clips with no Signals row → skip_reason derived from empty timeline."""
    creator = _creator()
    video = _video(creator.id)
    _set_overrides(creator, video, [], signals=None)

    try:
        with patch("preference.train.load_latest", new=AsyncMock(return_value=None)):
            resp = client.get(f"/videos/{video.id}/clips", cookies={"session": "x"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    # No signals row → empty timeline dict → no signal
    assert body["skip_reason"] == SKIP_REASON_NO_SIGNAL


def test_list_clips_with_clips_has_no_skip_reason(client):
    """GET /videos/{id}/clips with existing clips → skip_reason is None."""
    from models import Clip, RenderStatus

    creator = _creator()
    video = _video(creator.id)
    clip = MagicMock(spec=Clip)
    clip.id = uuid.uuid4()
    clip.video_id = video.id
    clip.creator_id = creator.id
    clip.setup_start_s = 10.0
    clip.start_s = 10.0
    clip.end_s = 70.0
    clip.peak_s = 55.0
    clip.score = 0.8
    clip.rank = 1
    clip.signals_jsonb = {"principle": "Hook in the first 3 seconds", "reasoning": "Strong hook."}
    clip.render_status = RenderStatus.pending
    clip.render_uri = None
    clip.cleaned_render_uri = None

    _set_overrides(creator, video, [clip])

    try:
        with patch("preference.train.load_latest", new=AsyncMock(return_value=None)):
            resp = client.get(f"/videos/{video.id}/clips", cookies={"session": "x"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["skip_reason"] is None
    assert body["skip_reason_label"] is None


def test_list_clips_skip_reason_label_no_virality(client):
    """skip_reason_label in the HTTP response must not contain virality language."""
    creator = _creator()
    video = _video(creator.id)
    tl = {"duration_s": 0.0, "events": []}
    sigs = _signals(video.id, tl)
    _set_overrides(creator, video, [], sigs)

    try:
        with patch("preference.train.load_latest", new=AsyncMock(return_value=None)):
            resp = client.get(f"/videos/{video.id}/clips", cookies={"session": "x"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body_text = resp.text.lower()
    for term in _VIRALITY_TERMS:
        assert term not in body_text, f"Found virality term {term!r} in /videos/<id>/clips response"


# ── (e) Per-creator isolation ─────────────────────────────────────────────────


def test_list_clips_skip_reason_does_not_return_other_creators_video(client):
    """skip_reason derivation never exposes another creator's Signals row.

    The Signals lookup is gated by the Video.creator_id check that already
    precedes it — a different creator's video raises 404 before we read Signals.
    This test documents that the 404 guard is in place and does not silently
    fall through to skip_reason derivation.
    """
    creator_a = _creator()
    creator_b = _creator()

    # video belongs to creator_a but request is made as creator_b
    video_a = _video(creator_a.id)

    async def _session():
        session = AsyncMock()

        # video_a belongs to creator_a but creator_b is the requester — the
        # ownership-scoped select's predicate filters it out (Issue 109e).
        session.execute = AsyncMock(
            side_effect=lambda stmt, *a, **kw: owned_lookup_result(stmt, video_a)
        )
        yield session

    app.dependency_overrides[get_current_creator] = lambda: creator_b
    app.dependency_overrides[get_session] = _session

    try:
        with patch("preference.train.load_latest", new=AsyncMock(return_value=None)):
            resp = client.get(f"/videos/{video_a.id}/clips", cookies={"session": "x"})
    finally:
        app.dependency_overrides.clear()

    # creator_b requesting creator_a's video → 404, not skip_reason exposure
    assert resp.status_code == 404
