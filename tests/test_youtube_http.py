"""
Unit tests for Issue 72 — shared YouTube HTTP client + 5xx backoff.

DB/redis-free: the quota `consume` and the backoff `sleep` are patched, and an
httpx MockTransport stands in for Google (no network).
"""

from unittest.mock import AsyncMock

import httpx
import pytest

from youtube import _http


@pytest.mark.asyncio
async def test_client_is_a_singleton_recreated_after_close():
    a = _http.client()
    b = _http.client()
    assert a is b  # reused, not rebuilt per call
    assert a.timeout.connect == 5.0 and a.timeout.read == 15.0

    await _http.aclose()
    c = _http.client()
    assert c is not a  # fresh instance after close
    await _http.aclose()


@pytest.mark.asyncio
async def test_get_json_retries_on_5xx(mocker):
    from youtube import data_api

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503)  # transient — must back off and retry
        return httpx.Response(200, json={"ok": True})

    fake = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    mocker.patch("youtube._http.client", return_value=fake)
    mocker.patch("youtube.data_api.consume", new=AsyncMock())  # skip quota/redis
    mocker.patch("asyncio.sleep", new=AsyncMock())  # no real backoff wait

    result = await data_api._get_json("token", "https://api.example/x", {})

    assert result == {"ok": True}
    assert calls["n"] == 2  # retried exactly once after the 503
    await fake.aclose()
