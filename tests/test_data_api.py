"""
Unit tests for youtube/data_api.py — Issue 260 ETag/304 quota-free path + fields=.

DB/redis-free: `consume` is patched (so quota/redis is skipped) and an httpx
MockTransport stands in for Google (no network). The ETag Redis cache helpers
are patched at their module boundary.
"""

import uuid
from unittest.mock import AsyncMock

import httpx
import pytest

from youtube import data_api


def _fake_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


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
    mocker.patch(
        "youtube.data_api._etag_cache_get", new=AsyncMock(return_value=(None, None))
    )
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
                json={
                    "items": [
                        {"contentDetails": {"relatedPlaylists": {"uploads": "UP1"}}}
                    ]
                },
            )
        return httpx.Response(200, json={"items": []})

    fake = _fake_client(handler)
    mocker.patch("youtube._http.client", return_value=fake)
    mocker.patch("youtube.data_api.consume", new=AsyncMock())
    mocker.patch(
        "youtube.data_api._etag_cache_get", new=AsyncMock(return_value=(None, None))
    )
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
    mocker.patch(
        "youtube.data_api._etag_cache_get", new=AsyncMock(return_value=(None, None))
    )
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
