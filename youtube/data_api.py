"""
YouTube Data API v3 helpers.

All external HTTP calls go through _get_json() so tests can patch a single point.
"""

import asyncio
import logging
import random
import re

import httpx

from config import settings
from models import VideoKind
from youtube import _http
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


def parse_duration_seconds(iso_duration: str) -> float:
    """Parse ISO 8601 duration string (e.g. PT1H30M15S) to total seconds."""
    pattern = re.compile(
        r"P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?",
        re.IGNORECASE,
    )
    m = pattern.match(iso_duration)
    if not m:
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
    access_token: str, url: str, params: dict, cost: int = COST_DATA_VIDEOS
) -> dict:
    await consume(cost)
    headers = {"Authorization": f"Bearer {access_token}"}
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

        if resp.status_code < 400:
            return resp.json()

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


async def get_uploads_playlist_id(access_token: str) -> str:
    data = await _get_json(
        access_token,
        f"{_YOUTUBE_V3}/channels",
        {"part": "contentDetails", "mine": "true"},
        cost=COST_DATA_CHANNELS,
    )
    items = data.get("items", [])
    if not items:
        raise ValueError("No channel found for this access token")
    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


async def list_channel_videos(access_token: str) -> list[dict]:
    """Return all video IDs + metadata from the uploads playlist (paginated)."""
    playlist_id = await get_uploads_playlist_id(access_token)
    results: list[dict] = []
    page_token: str | None = None

    while True:
        params: dict = {
            "part": "snippet",
            "playlistId": playlist_id,
            "maxResults": _MAX_RESULTS,
        }
        if page_token:
            params["pageToken"] = page_token

        data = await _get_json(
            access_token, f"{_YOUTUBE_V3}/playlistItems", params, cost=COST_DATA_PLAYLIST_ITEMS
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
                    }
                )

        page_token = data.get("nextPageToken")
        if not page_token:
            break

    return results


async def get_videos_metadata(access_token: str, video_ids: list[str]) -> list[dict]:
    """Fetch contentDetails (duration) for up to 50 video IDs per call."""
    if not video_ids:
        return []
    data = await _get_json(
        access_token,
        f"{_YOUTUBE_V3}/videos",
        {"part": "contentDetails", "id": ",".join(video_ids[:50])},
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


async def check_captions_available(access_token: str, video_id: str) -> bool:
    data = await _get_json(
        access_token,
        f"{_YOUTUBE_V3}/captions",
        {"part": "snippet", "videoId": video_id},
        cost=COST_DATA_CAPTIONS,
    )
    return bool(data.get("items"))


async def get_video_stats(access_token: str, youtube_video_id: str) -> dict:
    """Fetch view count for a single published video. Returns {} if not found."""
    data = await _get_json(
        access_token,
        f"{_YOUTUBE_V3}/videos",
        {"part": "statistics", "id": youtube_video_id},
    )
    items = data.get("items", [])
    if not items:
        return {}
    stats = items[0].get("statistics", {})
    return {"views": int(stats.get("viewCount", 0))}
