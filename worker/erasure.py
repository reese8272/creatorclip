"""Media-key enumeration + purge posture for archive and right-to-erasure
(Issues 446 + 471, OFF_COURSE_BUGS L143).

Why enumeration is DB URIs ∪ deterministically CONSTRUCTED keys, not either
alone: the DB pointer is authoritative for keys that embed a random token
(``source/…``), but render keys are pure functions of the row id — and the DB
pointer to them is not stable. ``POST /clips/{id}/clean/confirm`` swaps
``cleaned_render_uri`` into ``render_uri``, orphaning the original
``clips/{id}.mp4`` with NO row referencing it (L143), and a failed task can
upload before its DB write lands. Reconstructing every deterministic key from
the surviving row reaches those orphans without any bookkeeping table; the
delete of a key that was never written is a free no-op (S3 DELETE semantics).

``KEY_PATTERNS`` is the grep-verified inventory of every R2 write site in the
codebase (Issue 471 acceptance). tests/test_erasure_keys.py asserts each
pattern has a concrete representative in the enumeration — extend BOTH when
adding a write site.
"""

import asyncio
import logging
import uuid
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from config import settings

if TYPE_CHECKING:
    from models import Clip, Summary, Video

logger = logging.getLogger(__name__)

# Every storage write key in the codebase, by pattern (write sites annotated).
# {token} is the YouTube id or a uuid4 hex; {suffix} a whitelisted container.
KEY_PATTERNS: dict[str, str] = {
    "source": "source/{creator_id}/{token}{suffix}",  # routers/videos.py uploads
    "audio": "audio/{video_id}.wav",  # worker/tasks.py _ingest
    "video_poster": "posters/{creator_id}/{video_id}.jpg",  # ingest + backfill
    "video_peaks": "peaks/{creator_id}/{video_id}.json",  # ingest + backfill
    "clip_render": "clips/{clip_id}.mp4",  # _encode_and_upload_clip
    "clip_clean": "clips/{clip_id}_clean.mp4",  # _clean_clip_async
    "clip_edit": "clips/{clip_id}_edit.mp4",  # _edit_clip_async
    "clip_poster": "posters/{creator_id}/clip-{clip_id}.jpg",  # render poster
    "export": "exports/{creator_id}/{job_id}.json",  # generate_data_export
    "summary_render": "summaries/{summary_id}.mp4",  # _render_summary_async
}


def _uri_for_key(key: str) -> str:
    """The URI ``upload_file(…, key)`` would have returned for this backend —
    so constructed keys are deletable through the same ``delete_file`` surface
    as the URIs stored in DB columns."""
    if settings.STORAGE_BACKEND == "r2":
        return f"s3://{settings.R2_BUCKET}/{key}"
    from worker.storage import _local_root

    return str(_local_root() / key)


def _key_of(uri: str) -> str:
    """Extract the storage key a URI addresses (inverse of ``_uri_for_key``)."""
    if uri.startswith("s3://"):
        rest = uri[5:]
        return rest.split("/", 1)[1] if "/" in rest else rest
    from worker.storage import _local_root

    root = str(_local_root())
    if uri.startswith(root):
        return uri[len(root) :].lstrip("/")
    return uri


def _dedupe_by_key(uris: Iterable[str]) -> set[str]:
    """One URI per addressed object. Callers list DB pointers BEFORE constructed
    keys, so when both address the same key the stored pointer wins — it is the
    form that was actually written."""
    seen: dict[str, str] = {}
    for uri in uris:
        seen.setdefault(_key_of(uri), uri)
    return set(seen.values())


def candidate_keys_for_clip(clip: "Clip | Any", *, include_posters: bool = True) -> set[str]:
    """Every storage URI a clip's artifacts may live at.

    Union of the DB pointers (authoritative when present) and the constructed
    deterministic keys (reach clean-confirm orphans — L143). ``include_posters=
    False`` is the ARCHIVE default: the review-card poster survives archive so
    a restored row is recognisable; only permanent erasure passes True.
    """
    uris = [uri for uri in (clip.render_uri, clip.cleaned_render_uri) if uri is not None]
    uris += [
        _uri_for_key(f"clips/{clip.id}.mp4"),
        _uri_for_key(f"clips/{clip.id}_clean.mp4"),
        _uri_for_key(f"clips/{clip.id}_edit.mp4"),
    ]
    if include_posters:
        if clip.poster_uri is not None:
            uris.append(clip.poster_uri)
        uris.append(_uri_for_key(f"posters/{clip.creator_id}/clip-{clip.id}.jpg"))
    return _dedupe_by_key(uris)


def candidate_keys_for_video(
    video: "Video | Any",
    clips: Iterable["Clip | Any"],
    *,
    include_identity_artifacts: bool = True,
) -> set[str]:
    """Every storage URI a video (plus its clips) may occupy.

    ``include_identity_artifacts=False`` is the ARCHIVE default (owner ruling,
    Issue 446): posters + waveform peaks are index-entry artifacts — the
    COMPLIANCE.md data class that deliberately outlives the source purge — and
    they are exactly what lets a creator recognise an archived row to restore
    it. Permanent erasure (and account deletion) passes True.
    """
    uris = [uri for uri in (video.source_uri, video.audio_uri) if uri is not None]
    uris.append(_uri_for_key(f"audio/{video.id}.wav"))
    if include_identity_artifacts:
        uris += [uri for uri in (video.poster_uri, video.peaks_uri) if uri is not None]
    out = _dedupe_by_key(uris)
    for clip in clips:
        out |= candidate_keys_for_clip(clip, include_posters=include_identity_artifacts)
    return out


def summary_keys(summary: "Summary | Any") -> set[str]:
    """DB pointer ∪ constructed key for one recap artifact."""
    uris = [] if summary.render_uri is None else [summary.render_uri]
    uris.append(_uri_for_key(f"summaries/{summary.id}.mp4"))
    return _dedupe_by_key(uris)


async def candidate_keys_for_creator(session: Any, creator_id: uuid.UUID) -> set[str]:
    """Every storage URI held for one creator — the right-to-erasure delete set
    (Issue 471). Enumerates from the DB (all videos incl. archived, all clips,
    recap summaries, GDPR export artifacts) rather than prefix-guessing; the
    creator-scoped prefix sweeps in ``erase_creator`` stay as belt-and-braces.
    """
    from sqlalchemy import select

    from models import Clip, DataExport, Summary, Video

    videos = (
        (await session.execute(select(Video).where(Video.creator_id == creator_id)))
        .scalars()
        .all()
    )
    clips = (
        (await session.execute(select(Clip).where(Clip.creator_id == creator_id)))
        .scalars()
        .all()
    )
    summaries = (
        (await session.execute(select(Summary).where(Summary.creator_id == creator_id)))
        .scalars()
        .all()
    )
    export_uris = (
        (
            await session.execute(
                select(DataExport.export_uri).where(DataExport.creator_id == creator_id)
            )
        )
        .scalars()
        .all()
    )

    uris: set[str] = set()
    for video in videos:
        uris |= candidate_keys_for_video(video, [])
    for clip in clips:
        uris |= candidate_keys_for_clip(clip)
    for summary in summaries:
        uris |= summary_keys(summary)
    uris |= {uri for uri in export_uris if uri is not None}
    return uris


async def purge_uris(uris: Iterable[str]) -> set[str]:
    """Best-effort delete of each URI independently; returns the successes.

    Purge posture copied from ``_purge_stale_source_media_async``: one blob's
    failure (Object-Lock refusal on ``clips/`` per Issue 258, or a transient
    storage error) is logged and skipped, never raised — the caller must keep
    the DB pointer for any URI NOT in the returned set so a later sweep or a
    repeated erase can retry it. A missing object counts as success: local
    ``delete_file`` no-ops on absent paths and S3/R2 DELETE returns 204 for
    nonexistent keys, which is the goal state anyway.
    """
    from worker.storage import adelete_file

    ok: set[str] = set()
    for uri in uris:
        try:
            await adelete_file(uri)
            ok.add(uri)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Erasure purge failed for %s: %s", uri, exc)
    return ok
