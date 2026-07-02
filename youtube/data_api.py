"""
YouTube Data API v3 helpers.

All external HTTP calls go through _get_json() so tests can patch a single point.
"""

import asyncio
import hashlib
import json
import logging
import random
import re
import uuid

import httpx

from config import settings
from models import VideoKind
from youtube import _http
from youtube._redis import get_redis_client
from youtube.errors import (
    PERMANENT_403_REASONS,
    TRANSIENT_403_REASONS,
    YouTubeAuthError,
    retry_after_seconds,
)
from youtube.quota import (
    COST_DATA_CAPTIONS,
    COST_DATA_CHANNELS,
    COST_DATA_PLAYLIST_ITEMS,
    COST_DATA_VIDEOS,
    consume,
)

logger = logging.getLogger(__name__)

_MAX_RETRIES = 4

_YOUTUBE_V3 = "https://www.googleapis.com/youtube/v3"
_MAX_RESULTS = 50

# fields= field-filters (Google's partial-response syntax) trim the response to
# only the keys we persist. This cuts bandwidth/parse cost (Google cites 50-80%
# smaller payloads); it does NOT reduce quota units (those are fixed per method
# server-side — the quota lever is the ETag 304 path + batching). (Issue 260)
_FIELDS_PLAYLIST_ITEMS = (
    "nextPageToken,items(snippet(resourceId/videoId,title,description,publishedAt))"
)
_FIELDS_VIDEOS_CONTENT_DETAILS = "items(id,contentDetails/duration)"
_FIELDS_VIDEOS_STATISTICS = "items(id,statistics/viewCount)"


def _etag_cache_key(url: str, params: dict, creator_id: uuid.UUID | None) -> str:
    """Deterministic per-creator cache key from url+params (+creator_id).

    Per-creator isolation: the URL+params already embed the resource id /
    playlistId, but we also fold in creator_id so no cross-creator body reuse is
    ever possible even if two creators query an overlapping public resource.
    """
    canonical = json.dumps(
        {"url": url, "params": dict(sorted(params.items())), "creator": str(creator_id)},
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"creatorclip:yt_etag:{digest}"


async def _etag_cache_get(cache_key: str) -> tuple[str | None, dict | None]:
    """Return (etag, body) for a cached conditional GET, or (None, None) on miss."""
    r = get_redis_client()
    raw = await r.get(cache_key)
    if not raw:
        return None, None
    try:
        payload = json.loads(raw)
        return payload.get("etag"), payload.get("body")
    except (ValueError, TypeError):
        return None, None


async def _etag_cache_put(cache_key: str, etag: str, body: dict, ttl: int) -> None:
    """Store an ETag + response body for future If-None-Match conditional GETs."""
    if not etag:
        return
    r = get_redis_client()
    await r.set(cache_key, json.dumps({"etag": etag, "body": body}), ex=ttl)


def parse_duration_seconds(iso_duration: str) -> float:
    """Parse ISO 8601 duration string (e.g. PT1H30M15S) to total seconds."""
    pattern = re.compile(
        r"P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?",
        re.IGNORECASE,
    )
    m = pattern.match(iso_duration)
    if not m:
        logger.warning(
            "parse_duration_seconds: unrecognized ISO-8601 duration %r — returning 0.0",
            iso_duration,
        )
        return 0.0
    days, hours, minutes, seconds = m.groups(default="0")
    return float(days) * 86400 + float(hours) * 3600 + float(minutes) * 60 + float(seconds)


def classify_video_kind(duration_s: float) -> VideoKind:
    return VideoKind.short if duration_s <= settings.SHORTS_MAX_DURATION_S else VideoKind.long


def clamp_ingest_field(value: str | None, max_chars: int) -> str | None:
    """Truncate and normalize a raw ingest string to at most max_chars characters.

    Applies the same safe-truncation pattern as dna/identity.py (word-boundary
    rsplit + whitespace normalization) so multi-byte characters are never split
    mid-sequence.  Returns None unchanged.

    Designed for ingest-side clamping of YouTube-sourced strings (Issue 227).
    Prevents adversarially-crafted or pathologically-long values from acting as
    injection-payload carriers (OWASP LLM01) or creating a token-cost / DoS vector
    when the value later enters the prompt corpus.

    Args:
        value: The raw string from the YouTube API response (or None).
        max_chars: Maximum character length.  Values at or below this length are
                   whitespace-normalized but NOT truncated.

    Returns:
        Whitespace-normalized string truncated to at most max_chars characters,
        or None if value is None.
    """
    if value is None:
        return None
    # Normalize whitespace first — collapse runs, strip leading/trailing.
    normalized = " ".join(value.split())
    if len(normalized) <= max_chars:
        return normalized
    # Truncate at a word boundary: rsplit at the first space from max_chars
    # backward so we never cut in the middle of a multi-byte sequence or word.
    truncated = normalized[:max_chars].rsplit(" ", 1)[0]
    return truncated or normalized[:max_chars]


def _classify_error(resp: httpx.Response) -> tuple[str, bool]:
    """Return (reason, is_transient) for a non-2xx YouTube API response.

    is_transient=True ⇒ retry with backoff. False ⇒ raise YouTubeAuthError.
    """
    try:
        reason = resp.json()["error"]["errors"][0]["reason"]
    except (ValueError, KeyError, IndexError, TypeError):
        reason = ""

    if resp.status_code == 429:
        return reason or "rateLimitExceeded", True
    if resp.status_code == 401:
        return reason or "authError", False
    if resp.status_code == 403:
        if reason in TRANSIENT_403_REASONS:
            return reason, True
        if reason in PERMANENT_403_REASONS:
            return reason, False
        # Unknown 403 reason — treat conservatively as permanent so we don't
        # spin on a misconfigured grant.
        return reason or "forbidden", False
    return reason, False


async def _get_json(
    access_token: str,
    url: str,
    params: dict,
    cost: int = COST_DATA_VIDEOS,
    *,
    creator_id: uuid.UUID | None = None,
    sub_budget: int | None = None,
    etag_cache: bool = False,
) -> dict:
    """Issue 260: quota consume is now deferred until AFTER the 304 decision.

    A `304 Not Modified` from a conditional (If-None-Match) GET returns the
    cached body and spends NO quota — mirroring Google's conditional-request
    semantics where a 304 is free. This is the measurable quota-unit reduction
    lever. On a `200`, consume(cost) is charged once and the new ETag+body are
    cached. `creator_id`/`sub_budget` thread the per-creator refresh sub-budget
    through to consume().
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    cache_key: str | None = None
    cached_body: dict | None = None
    if etag_cache:
        cache_key = _etag_cache_key(url, params, creator_id)
        cached_etag, cached_body = await _etag_cache_get(cache_key)
        if cached_etag:
            headers["If-None-Match"] = cached_etag
    delay = 1.0

    for attempt in range(_MAX_RETRIES):
        # Shared timeout-bounded client, reused across calls/retries (Issue 72).
        # Issue 88: network-level errors (ReadTimeout, ConnectError) bypass
        # the HTTP-status retry arm below. Catch them as transient → backoff.
        try:
            resp = await _http.client().get(url, headers=headers, params=params)
        except httpx.RequestError as exc:
            if attempt < _MAX_RETRIES - 1:
                jitter = random.uniform(0, delay * 0.3)
                await asyncio.sleep(delay + jitter)
                delay *= 2
                continue
            logger.warning(
                "YouTube Data API request error %s after %d retries: %r",
                url,
                _MAX_RETRIES,
                exc,
            )
            raise

        # A 304 means the cached body is still fresh — return it without spending
        # any quota (Issue 260). Google charges nothing for a conditional 304.
        if resp.status_code == 304 and cached_body is not None:
            logger.debug("YouTube Data API 304 (cache hit) %s — no quota spent", url)
            return cached_body

        if resp.status_code < 400:
            # Charge quota only now that we know it is a real (non-304) response.
            await consume(cost, creator_id=creator_id, sub_budget=sub_budget)
            body = resp.json()
            if cache_key is not None:
                etag = resp.headers.get("ETag", "")
                await _etag_cache_put(cache_key, etag, body, settings.YOUTUBE_ETAG_CACHE_TTL_S)
            return body

        if resp.status_code in (401, 403, 429):
            reason, is_transient = _classify_error(resp)
            if not is_transient:
                raise YouTubeAuthError(reason, resp.status_code)
            if attempt < _MAX_RETRIES - 1:
                jitter = random.uniform(0, delay * 0.3)
                base = delay + jitter
                # Honor a server-stated Retry-After (Google sends it on 429). (Issue A)
                retry_after = retry_after_seconds(resp)
                sleep_s = max(retry_after, base) if retry_after is not None else base
                await asyncio.sleep(sleep_s)
                delay *= 2
                continue
            logger.warning(
                "YouTube Data API %s returned %s (reason=%s) after %d retries",
                url,
                resp.status_code,
                reason,
                _MAX_RETRIES,
            )
        elif resp.status_code >= 500 and attempt < _MAX_RETRIES - 1:
            # 5xx is transient for these idempotent GETs — back off and retry (axis E).
            jitter = random.uniform(0, delay * 0.3)
            await asyncio.sleep(delay + jitter)
            delay *= 2
            continue

        resp.raise_for_status()
        return resp.json()

    resp.raise_for_status()
    return {}  # unreachable


async def get_uploads_playlist_id(access_token: str, *, creator_id: uuid.UUID | None = None) -> str:
    data = await _get_json(
        access_token,
        f"{_YOUTUBE_V3}/channels",
        {"part": "contentDetails", "mine": "true"},
        cost=COST_DATA_CHANNELS,
        creator_id=creator_id,
    )
    items = data.get("items", [])
    if not items:
        raise ValueError("No channel found for this access token")
    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


async def list_channel_videos(
    access_token: str, *, creator_id: uuid.UUID | None = None
) -> list[dict]:
    """Return all video IDs + metadata from the uploads playlist (paginated)."""
    playlist_id = await get_uploads_playlist_id(access_token, creator_id=creator_id)
    results: list[dict] = []
    page_token: str | None = None

    while True:
        params: dict = {
            "part": "snippet",
            "playlistId": playlist_id,
            "maxResults": _MAX_RESULTS,
            "fields": _FIELDS_PLAYLIST_ITEMS,
        }
        if page_token:
            params["pageToken"] = page_token

        data = await _get_json(
            access_token,
            f"{_YOUTUBE_V3}/playlistItems",
            params,
            cost=COST_DATA_PLAYLIST_ITEMS,
            creator_id=creator_id,
            etag_cache=True,
        )
        for item in data.get("items", []):
            snippet = item.get("snippet", {})
            resource_id = snippet.get("resourceId", {})
            if resource_id.get("kind") == "youtube#video":
                results.append(
                    {
                        "video_id": resource_id["videoId"],
                        # Issue 227: clamp title at ingest to prevent adversarially-
                        # crafted titles acting as injection carriers or token-cost
                        # vectors when they enter the prompt corpus. YouTube's own
                        # published limit is 100 chars; the 2× margin only truncates
                        # pathological/synthetic inputs (OWASP LLM01).
                        "title": clamp_ingest_field(
                            snippet.get("title"), settings.MAX_INGESTED_TITLE_CHARS
                        ),
                        "published_at": snippet.get("publishedAt"),
                        # Issue 227: description is NOT stored on the Video model and is
                        # not currently persisted anywhere — but the YouTube playlistItems
                        # snippet always includes it. Clamp at the ingest boundary now so
                        # that if description storage is added later the guard is already
                        # in place and cannot be forgotten (defensive / future-proofing).
                        # Callers that don't need the description can ignore this key.
                        "description": clamp_ingest_field(
                            snippet.get("description"), settings.MAX_INGESTED_DESC_CHARS
                        ),
                    }
                )

        page_token = data.get("nextPageToken")
        if not page_token:
            break

    return results


async def get_videos_metadata(
    access_token: str, video_ids: list[str], *, creator_id: uuid.UUID | None = None
) -> list[dict]:
    """Fetch contentDetails (duration) for up to 50 video IDs per call."""
    if not video_ids:
        return []
    data = await _get_json(
        access_token,
        f"{_YOUTUBE_V3}/videos",
        {
            "part": "contentDetails",
            "id": ",".join(video_ids[:50]),
            "fields": _FIELDS_VIDEOS_CONTENT_DETAILS,
        },
        creator_id=creator_id,
        etag_cache=True,
    )
    results = []
    for item in data.get("items", []):
        duration_s = parse_duration_seconds(item["contentDetails"]["duration"])
        results.append(
            {
                "video_id": item["id"],
                "duration_s": duration_s,
                "kind": classify_video_kind(duration_s),
            }
        )
    return results


async def check_captions_available(
    access_token: str, video_id: str, *, creator_id: uuid.UUID | None = None
) -> bool:
    data = await _get_json(
        access_token,
        f"{_YOUTUBE_V3}/captions",
        {"part": "snippet", "videoId": video_id},
        cost=COST_DATA_CAPTIONS,
        creator_id=creator_id,
    )
    return bool(data.get("items"))


async def get_video_stats(
    access_token: str, youtube_video_id: str, *, creator_id: uuid.UUID | None = None
) -> dict:
    """Fetch view count for a single published video. Returns {} if not found."""
    data = await _get_json(
        access_token,
        f"{_YOUTUBE_V3}/videos",
        {
            "part": "statistics",
            "id": youtube_video_id,
            "fields": _FIELDS_VIDEOS_STATISTICS,
        },
        creator_id=creator_id,
    )
    items = data.get("items", [])
    if not items:
        return {}
    stats = items[0].get("statistics", {})
    return {"views": int(stats.get("viewCount", 0))}
