"""Tests for GET /creators/me/insights/analytics (Issue 115)."""

import uuid
from unittest.mock import AsyncMock, MagicMock

from auth import get_current_creator
from db import get_session
from main import app


def _mock_creator() -> MagicMock:
    c = MagicMock()
    c.id = uuid.uuid4()
    return c


def _fake_session(row_tuple: tuple):
    """Return a dependency override that yields a session whose execute()
    returns the provided row tuple (cnt, total_views, total_watch_s, avg_dur, avg_eng)."""

    async def _session():
        session = AsyncMock()
        result = MagicMock()
        result.one.return_value = row_tuple
        session.execute = AsyncMock(return_value=result)
        yield session

    return _session


def test_analytics_summary_default_period(client):
    """GET /creators/me/insights/analytics with no params uses 28d."""
    creator = _mock_creator()
    row = (5, 10_000, 36_000, 120.5, 0.042)
    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _fake_session(row)
    try:
        resp = client.get("/creators/me/insights/analytics", cookies={"session": "x"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert data["period"] == "28d"
    assert data["videos_in_period"] == 5
    assert data["total_views"] == 10_000
    assert data["total_watch_time_h"] == 10.0
    assert data["avg_view_duration_s"] == 120.5
    assert data["metrics_available"] is True


def test_analytics_summary_explicit_period(client):
    """Period param is forwarded correctly."""
    creator = _mock_creator()
    row = (2, 500, 1800, 90.0, 0.03)
    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _fake_session(row)
    try:
        resp = client.get("/creators/me/insights/analytics?period=7d", cookies={"session": "x"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["period"] == "7d"


def test_analytics_summary_empty_returns_zero_not_404(client):
    """When no videos have metrics yet, return zeros with metrics_available=False."""
    creator = _mock_creator()
    row = (0, 0, 0, None, None)
    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _fake_session(row)
    try:
        resp = client.get("/creators/me/insights/analytics", cookies={"session": "x"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert data["metrics_available"] is False
    assert data["total_views"] == 0


def test_analytics_summary_invalid_period_422(client):
    """An unknown period string is rejected by the Query validator."""
    creator = _mock_creator()
    app.dependency_overrides[get_current_creator] = lambda: creator
    try:
        resp = client.get("/creators/me/insights/analytics?period=1y", cookies={"session": "x"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 422


def test_analytics_summary_requires_auth(client):
    resp = client.get("/creators/me/insights/analytics")
    assert resp.status_code == 401
