"""
Unit tests for youtube/data_api.py — Issue 260 ETag/304 quota-free path + fields=.

DB/redis-free: `consume` is patched (so quota/redis is skipped) and an httpx
MockTransport stands in for Google (no network). The ETag Redis cache helpers
are patched at their module boundary.
"""

import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from youtube import data_api

FIXTURES = Path(__file__).parent / "fixtures"


def _fake_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ── fields= contract guard ────────────────────────────────────────────────────
#
# Why this section exists. `_FIELDS_PLAYLIST_ITEMS` omitted `resourceId/kind`
# while `list_channel_videos` filtered items on exactly that key. A `fields` spec
# returns ONLY the properties it names, so every item was dropped and
# `sync_video_catalog` imported nothing — HTTP 200, no error, "Synced N video(s)"
# in the worker log, for ~7 weeks and for every creator.
#
# Two things let it through, and both are fixed here rather than in prose:
#   1. `yt_playlist_items.json` hand-writes a response the real request cannot
#      produce (it carries `kind`), and NOTHING referenced it — the parser's
#      non-empty path had zero coverage.
#   2. The only playlistItems test asserted merely that a `fields` key was sent,
#      never that the response it produces is parseable.
#
# The guard: simulate Google's partial-response filter, apply the REAL `_FIELDS_*`
# spec to the fixture, and parse THAT. A key the parser reads but the spec does
# not request now fails here instead of silently in production.


def _parse_fields(spec: str) -> dict:
    """Parse a Google partial-response ``fields`` spec into a nested selector tree.

    Grammar, per https://developers.google.com/youtube/v3/getting-started:
      ``a,b``    — select multiple fields
      ``a(b,c)`` — select a group of nested properties
      ``a/b``    — select one nested property (sugar for ``a(b)``)

    A leaf maps to ``None``, meaning "take this subtree whole".
    """

    def selector(pos: int) -> tuple[str, dict | None, int]:
        start = pos
        while pos < len(spec) and spec[pos] not in ",()/":
            pos += 1
        name = spec[start:pos]
        if pos < len(spec) and spec[pos] == "/":
            sub_name, sub_child, pos = selector(pos + 1)
            return name, {sub_name: sub_child}, pos
        if pos < len(spec) and spec[pos] == "(":
            child, pos = group(pos + 1)
            return name, child, pos + 1  # consume the ")"
        return name, None, pos

    def group(pos: int) -> tuple[dict, int]:
        out: dict = {}
        while pos < len(spec) and spec[pos] != ")":
            if spec[pos] == ",":
                pos += 1
                continue
            name, child, pos = selector(pos)
            if name not in out:
                out[name] = child
            elif out[name] is None or child is None:
                out[name] = None  # one form takes the subtree whole; it wins
            else:
                out[name].update(child)  # e.g. `a/b,a/c` names one parent twice
        return out, pos

    return group(0)[0]


def _apply_fields(selectors: dict | None, body):
    """Return ``body`` trimmed to ``selectors``, the way Google's filter does."""
    if selectors is None:
        return body
    if isinstance(body, list):
        return [_apply_fields(selectors, item) for item in body]
    if not isinstance(body, dict):
        return body
    return {k: _apply_fields(sub, body[k]) for k, sub in selectors.items() if k in body}


def test_fields_simulator_matches_googles_documented_semantics():
    """Pin the simulator itself — every guard below is only as good as this.

    Example and rules taken from Google's partial-response documentation:
    https://developers.google.com/youtube/v3/getting-started
    """
    body = {
        "kind": "youtube#videoListResponse",
        "etag": "x",
        "items": [
            {
                "id": "v1",
                "etag": "x",
                "snippet": {
                    "channelId": "c",
                    "title": "t",
                    "categoryId": "22",
                    "description": "d",
                },
                "statistics": {"viewCount": "5", "likeCount": "1"},
            }
        ],
    }
    got = _apply_fields(
        _parse_fields("items(id,snippet(channelId,title,categoryId),statistics)"), body
    )
    assert got == {
        "items": [
            {
                "id": "v1",
                "snippet": {"channelId": "c", "title": "t", "categoryId": "22"},
                # `statistics` is a leaf in the spec → whole subtree survives
                "statistics": {"viewCount": "5", "likeCount": "1"},
            }
        ]
    }
    # Unnamed properties are ABSENT, not null — the property that caused the outage.
    assert "kind" not in got and "etag" not in got
    assert "description" not in got["items"][0]["snippet"]
    # `a/b` selects exactly one nested property.
    assert _apply_fields(_parse_fields("a/b"), {"a": {"b": 1, "c": 2}, "d": 3}) == {"a": {"b": 1}}


@pytest.mark.asyncio
async def test_list_channel_videos_parses_the_response_its_own_fields_spec_produces(mocker):
    """The regression guard for the 2026-06-24 → 2026-08-14 silent catalog outage.

    Fails with `videos == []` on the pre-fix spec, which is exactly the production
    symptom: a successful call that imports nothing.
    """
    filtered = _apply_fields(
        _parse_fields(data_api._FIELDS_PLAYLIST_ITEMS),
        json.loads((FIXTURES / "yt_playlist_items.json").read_text()),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if "channels" in request.url.path:
            return httpx.Response(
                200,
                json={"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UP1"}}}]},
            )
        return httpx.Response(200, json=filtered)

    fake = _fake_client(handler)
    mocker.patch("youtube._http.client", return_value=fake)
    mocker.patch("youtube.data_api.consume", new=AsyncMock())
    mocker.patch("youtube.data_api._etag_cache_get", new=AsyncMock(return_value=(None, None)))
    mocker.patch("youtube.data_api._etag_cache_put", new=AsyncMock())

    videos = await data_api.list_channel_videos("token", creator_id=uuid.uuid4())

    assert [v["video_id"] for v in videos] == ["video_long_1", "video_short_1"], (
        "the fields spec dropped every item — a key the parser reads is not requested"
    )
    assert videos[0]["title"] == "Long Form Video"
    assert videos[0]["published_at"] == "2026-01-15T12:00:00Z"
    await fake.aclose()


@pytest.mark.asyncio
async def test_get_videos_metadata_parses_the_response_its_own_fields_spec_produces(mocker):
    """Same contract, applied to the contentDetails parser."""
    filtered = _apply_fields(
        _parse_fields(data_api._FIELDS_VIDEOS_CONTENT_DETAILS),
        json.loads((FIXTURES / "yt_videos_metadata.json").read_text()),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=filtered)

    fake = _fake_client(handler)
    mocker.patch("youtube._http.client", return_value=fake)
    mocker.patch("youtube.data_api.consume", new=AsyncMock())
    mocker.patch("youtube.data_api._etag_cache_get", new=AsyncMock(return_value=(None, None)))
    mocker.patch("youtube.data_api._etag_cache_put", new=AsyncMock())

    meta = await data_api.get_videos_metadata("token", ["video_long_1", "video_short_1"])

    assert {m["video_id"]: m["duration_s"] for m in meta} == {
        "video_long_1": 754.0,
        "video_short_1": 58.0,
    }
    await fake.aclose()


@pytest.mark.asyncio
async def test_304_returns_cached_body_and_spends_no_quota(mocker):
    """A conditional 304 (If-None-Match match) returns the cache and never consumes quota."""
    cached = {"items": [{"id": "abc"}]}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("If-None-Match") == "etag-123"
        return httpx.Response(304)

    fake = _fake_client(handler)
    mocker.patch("youtube._http.client", return_value=fake)
    consume_mock = AsyncMock()
    mocker.patch("youtube.data_api.consume", new=consume_mock)
    mocker.patch(
        "youtube.data_api._etag_cache_get",
        new=AsyncMock(return_value=("etag-123", cached)),
    )
    put_mock = AsyncMock()
    mocker.patch("youtube.data_api._etag_cache_put", new=put_mock)

    result = await data_api._get_json("token", "https://api.example/x", {}, etag_cache=True)

    assert result == cached
    consume_mock.assert_not_awaited()  # 304 is free
    put_mock.assert_not_awaited()  # nothing new to store
    await fake.aclose()


@pytest.mark.asyncio
async def test_200_stores_etag_and_consumes_once(mocker):
    """A 200 charges quota exactly once and stores the returned ETag in the cache."""
    body = {"ok": True}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body, headers={"ETag": "new-etag"})

    fake = _fake_client(handler)
    mocker.patch("youtube._http.client", return_value=fake)
    consume_mock = AsyncMock()
    mocker.patch("youtube.data_api.consume", new=consume_mock)
    mocker.patch("youtube.data_api._etag_cache_get", new=AsyncMock(return_value=(None, None)))
    put_mock = AsyncMock()
    mocker.patch("youtube.data_api._etag_cache_put", new=put_mock)

    result = await data_api._get_json("token", "https://api.example/x", {}, etag_cache=True)

    assert result == body
    consume_mock.assert_awaited_once()
    put_mock.assert_awaited_once()
    # stored under (cache_key, etag, body, ttl)
    assert put_mock.await_args.args[1] == "new-etag"
    assert put_mock.await_args.args[2] == body
    await fake.aclose()


@pytest.mark.asyncio
async def test_list_channel_videos_sends_fields_param(mocker):
    captured: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.url)
        if "channels" in request.url.path:
            return httpx.Response(
                200,
                json={"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UP1"}}}]},
            )
        return httpx.Response(200, json={"items": []})

    fake = _fake_client(handler)
    mocker.patch("youtube._http.client", return_value=fake)
    mocker.patch("youtube.data_api.consume", new=AsyncMock())
    mocker.patch("youtube.data_api._etag_cache_get", new=AsyncMock(return_value=(None, None)))
    mocker.patch("youtube.data_api._etag_cache_put", new=AsyncMock())

    await data_api.list_channel_videos("token", creator_id=uuid.uuid4())

    playlist_urls = [u for u in captured if "playlistItems" in u.path]
    assert playlist_urls and "fields" in dict(playlist_urls[0].params)
    await fake.aclose()


@pytest.mark.asyncio
async def test_get_videos_metadata_sends_fields_and_batches_at_50(mocker):
    captured: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.url)
        return httpx.Response(200, json={"items": []})

    fake = _fake_client(handler)
    mocker.patch("youtube._http.client", return_value=fake)
    mocker.patch("youtube.data_api.consume", new=AsyncMock())
    mocker.patch("youtube.data_api._etag_cache_get", new=AsyncMock(return_value=(None, None)))
    mocker.patch("youtube.data_api._etag_cache_put", new=AsyncMock())

    ids = [f"v{i}" for i in range(60)]
    await data_api.get_videos_metadata("token", ids)

    params = dict(captured[0].params)
    assert "fields" in params
    # [:50] batch slice preserved — only 50 ids sent in the single call
    assert len(params["id"].split(",")) == 50
    await fake.aclose()


@pytest.mark.asyncio
async def test_get_video_stats_sends_fields_param(mocker):
    captured: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.url)
        return httpx.Response(200, json={"items": []})

    fake = _fake_client(handler)
    mocker.patch("youtube._http.client", return_value=fake)
    mocker.patch("youtube.data_api.consume", new=AsyncMock())

    await data_api.get_video_stats("token", "vid123")

    assert "fields" in dict(captured[0].params)
    await fake.aclose()
