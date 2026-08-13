"""Issue 446 + 471 — erasure key enumeration (worker/erasure.py).

The load-bearing test is the INVENTORY COVERAGE test: it builds a fixture graph
covering every R2 write site in the codebase and asserts that each of the ten
inventoried key patterns appears in the enumeration. Written RED against the
stub module first, so the enumeration can never silently drop a pattern.

Write-key inventory (grep-verified 2026-08-13):
  source/{creator_id}/{token}{suffix}      routers/videos.py (upload + multipart)
  audio/{video_id}.wav                     worker/tasks.py:1992
  posters/{creator_id}/{video_id}.jpg      worker/tasks.py:2029, :3817
  peaks/{creator_id}/{video_id}.json       worker/tasks.py:2008, :4049
  clips/{clip_id}.mp4                      worker/tasks.py:2572
  clips/{clip_id}_clean.mp4                worker/tasks.py:2844
  clips/{clip_id}_edit.mp4                 worker/tasks.py:2934
  posters/{creator_id}/clip-{clip_id}.jpg  worker/tasks.py:2580
  exports/{creator_id}/{job_id}.json       worker/tasks.py:5046
  summaries/{summary_id}.mp4               worker/tasks.py:6834
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from worker import erasure


def _covers(uris: set[str], key: str) -> bool:
    """True when some enumerated URI addresses ``key`` (URIs may be s3:// or
    local-disk absolute paths, so match on the key suffix)."""
    return any(u.endswith(key) for u in uris)


def _mock_clip(creator_id: uuid.UUID, video_id: uuid.UUID, **uris) -> MagicMock:
    clip = MagicMock()
    clip.id = uuid.uuid4()
    clip.creator_id = creator_id
    clip.video_id = video_id
    clip.render_uri = uris.get("render_uri")
    clip.cleaned_render_uri = uris.get("cleaned_render_uri")
    clip.poster_uri = uris.get("poster_uri")
    return clip


def _mock_video(creator_id: uuid.UUID, **uris) -> MagicMock:
    video = MagicMock()
    video.id = uuid.uuid4()
    video.creator_id = creator_id
    video.source_uri = uris.get("source_uri")
    video.audio_uri = uris.get("audio_uri")
    video.poster_uri = uris.get("poster_uri")
    video.peaks_uri = uris.get("peaks_uri")
    return video


def _mock_summary(creator_id: uuid.UUID, video_id: uuid.UUID, render_uri=None) -> MagicMock:
    s = MagicMock()
    s.id = uuid.uuid4()
    s.creator_id = creator_id
    s.video_id = video_id
    s.render_uri = render_uri
    return s


def _scalars_result(rows):
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    return result


# ── The inventory coverage test (RED-first) ───────────────────────────────────


@pytest.mark.asyncio
async def test_creator_enumeration_covers_every_inventoried_key_pattern():
    """EVERY one of the ten write-site key patterns must appear in the
    enumeration for a full fixture graph — including rows whose DB URI columns
    are NULL (orphaned objects reachable only via deterministic reconstruction,
    OFF_COURSE_BUGS L143 / clean-confirm orphan)."""
    creator_id = uuid.uuid4()

    # Video A: every DB URI populated (s3-shaped, as prod writes them).
    video_a = _mock_video(
        creator_id,
        source_uri=f"s3://media/source/{creator_id}/dQw4w9WgXcQ.mp4",
        audio_uri="s3://media/audio/PLACEHOLDER.wav",
        poster_uri=f"s3://media/posters/{creator_id}/PLACEHOLDER.jpg",
        peaks_uri=f"s3://media/peaks/{creator_id}/PLACEHOLDER.json",
    )
    video_a.audio_uri = f"s3://media/audio/{video_a.id}.wav"
    video_a.poster_uri = f"s3://media/posters/{creator_id}/{video_a.id}.jpg"
    video_a.peaks_uri = f"s3://media/peaks/{creator_id}/{video_a.id}.json"

    # Video B: all URI columns NULL (post-purge row) — audio key must still be
    # reconstructed.
    video_b = _mock_video(creator_id)

    # Clip A: DB URIs present. Clip B: all NULL — every render variant + the
    # poster must be reconstructed from the id alone (the clean-confirm orphan
    # shape: object on R2, no DB pointer).
    clip_a = _mock_clip(creator_id, video_a.id)
    clip_a.render_uri = f"s3://media/clips/{clip_a.id}.mp4"
    clip_a.cleaned_render_uri = f"s3://media/clips/{clip_a.id}_clean.mp4"
    clip_a.poster_uri = f"s3://media/posters/{creator_id}/clip-{clip_a.id}.jpg"
    clip_b = _mock_clip(creator_id, video_b.id)

    # Summary A: rendered. Summary B: row exists, render_uri NULL — key must be
    # reconstructed.
    summary_a = _mock_summary(creator_id, video_a.id)
    summary_a.render_uri = f"s3://media/summaries/{summary_a.id}.mp4"
    summary_b = _mock_summary(creator_id, video_b.id)

    export_job_id = uuid.uuid4().hex
    export_uri = f"s3://media/exports/{creator_id}/{export_job_id}.json"

    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[
            _scalars_result([video_a, video_b]),
            _scalars_result([clip_a, clip_b]),
            _scalars_result([summary_a, summary_b]),
            _scalars_result([export_uri]),
        ]
    )

    uris = await erasure.candidate_keys_for_creator(session, creator_id)

    expected = {
        "source": f"source/{creator_id}/dQw4w9WgXcQ.mp4",
        "audio": f"audio/{video_b.id}.wav",
        "video_poster": f"posters/{creator_id}/{video_a.id}.jpg",
        "video_peaks": f"peaks/{creator_id}/{video_a.id}.json",
        "clip_render": f"clips/{clip_b.id}.mp4",
        "clip_clean": f"clips/{clip_b.id}_clean.mp4",
        "clip_edit": f"clips/{clip_b.id}_edit.mp4",
        "clip_poster": f"posters/{creator_id}/clip-{clip_b.id}.jpg",
        "export": f"exports/{creator_id}/{export_job_id}.json",
        "summary_render": f"summaries/{summary_b.id}.mp4",
    }
    # The registry itself must name all ten patterns…
    assert set(expected) == set(erasure.KEY_PATTERNS), (
        "KEY_PATTERNS registry out of sync with the write-site inventory"
    )
    # …and every pattern must have a concrete representative in the enumeration.
    missing = {name: key for name, key in expected.items() if not _covers(uris, key)}
    assert not missing, f"enumeration misses inventoried key pattern(s): {missing}"


# ── Load-bearing edges ────────────────────────────────────────────────────────


def test_clip_enumeration_reconstructs_all_variants_without_db_uris():
    """L143: an orphaned render (clean-confirm swap) has NO DB pointer — the
    constructed keys are the only route to the bytes."""
    creator_id = uuid.uuid4()
    clip = _mock_clip(creator_id, uuid.uuid4())

    uris = erasure.candidate_keys_for_clip(clip)

    for key in (
        f"clips/{clip.id}.mp4",
        f"clips/{clip.id}_clean.mp4",
        f"clips/{clip.id}_edit.mp4",
        f"posters/{creator_id}/clip-{clip.id}.jpg",
    ):
        assert _covers(uris, key), f"missing constructed key {key}"


def test_clip_enumeration_dedupes_db_uri_against_constructed_key():
    """A DB URI that addresses the same object as a constructed key must
    appear once — the delete loop must not double-delete."""
    creator_id = uuid.uuid4()
    clip = _mock_clip(creator_id, uuid.uuid4())
    clip.render_uri = f"s3://media/clips/{clip.id}.mp4"

    uris = erasure.candidate_keys_for_clip(clip)

    matches = [u for u in uris if u.endswith(f"clips/{clip.id}.mp4")]
    assert len(matches) == 1


def test_archive_enumeration_excludes_posters_and_peaks():
    """Owner default (Issue 446): posters + peaks SURVIVE archive so a restored
    row is recognisable; only permanent erasure removes them."""
    creator_id = uuid.uuid4()
    video = _mock_video(
        creator_id,
        source_uri=f"s3://media/source/{creator_id}/abc.mp4",
        poster_uri=f"s3://media/posters/{creator_id}/vid.jpg",
        peaks_uri=f"s3://media/peaks/{creator_id}/vid.json",
    )
    video.audio_uri = f"s3://media/audio/{video.id}.wav"
    clip = _mock_clip(creator_id, video.id)
    clip.poster_uri = f"s3://media/posters/{creator_id}/clip-{clip.id}.jpg"

    uris = erasure.candidate_keys_for_video(video, [clip], include_identity_artifacts=False)

    assert not any("posters/" in u for u in uris), "clip/video posters must survive archive"
    assert not any("peaks/" in u for u in uris), "waveform peaks must survive archive"
    # …while the media that archive exists to free IS enumerated.
    assert _covers(uris, f"source/{creator_id}/abc.mp4")
    assert _covers(uris, f"audio/{video.id}.wav")
    assert _covers(uris, f"clips/{clip.id}.mp4")
    assert _covers(uris, f"clips/{clip.id}_clean.mp4")
    assert _covers(uris, f"clips/{clip.id}_edit.mp4")


@pytest.mark.asyncio
async def test_purge_uris_returns_only_successes_and_never_raises():
    """The purge posture (copied from _purge_stale_source_media_async): each
    blob is deleted independently; an Object-Lock refusal is logged and
    skipped so the caller keeps that DB pointer for a later retry."""
    from unittest.mock import patch

    deleted: list[str] = []

    def fake_delete(uri: str) -> None:
        if "locked" in uri:
            raise RuntimeError("AccessDenied: Object Lock")
        deleted.append(uri)

    with patch("worker.storage.delete_file", side_effect=fake_delete):
        ok = await erasure.purge_uris(
            ["s3://media/clips/a.mp4", "s3://media/clips/locked.mp4", "s3://media/audio/b.wav"]
        )

    assert ok == {"s3://media/clips/a.mp4", "s3://media/audio/b.wav"}
    assert deleted == ["s3://media/clips/a.mp4", "s3://media/audio/b.wav"]
