"""
YouTube Data API v3 helpers.

All external HTTP calls go through _get_json() so tests can patch a single point.
"""

import re

import httpx

from models import VideoKind

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
    return VideoKind.short if duration_s <= 60 else VideoKind.long


async def _get_json(access_token: str, url: str, params: dict) -> dict:
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=headers, params=params)
        resp.raise_for_status()
        return resp.json()


async def get_uploads_playlist_id(access_token: str) -> str:
    data = await _get_json(
        access_token,
        f"{_YOUTUBE_V3}/channels",
        {"part": "contentDetails", "mine": "true"},
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

        data = await _get_json(access_token, f"{_YOUTUBE_V3}/playlistItems", params)
        for item in data.get("items", []):
            snippet = item.get("snippet", {})
            resource_id = snippet.get("resourceId", {})
            if resource_id.get("kind") == "youtube#video":
                results.append(
                    {
                        "video_id": resource_id["videoId"],
                        "title": snippet.get("title"),
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
