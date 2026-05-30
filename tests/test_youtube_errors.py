"""Unit tests for youtube/errors.py helpers (Issue A — honor Retry-After)."""

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from unittest.mock import MagicMock

from youtube.errors import retry_after_seconds


def _resp(headers: dict) -> MagicMock:
    r = MagicMock()
    r.headers = headers
    return r


def test_retry_after_numeric_seconds():
    assert retry_after_seconds(_resp({"Retry-After": "30"})) == 30.0


def test_retry_after_missing_returns_none():
    assert retry_after_seconds(_resp({})) is None


def test_retry_after_garbage_returns_none():
    assert retry_after_seconds(_resp({"Retry-After": "soon"})) is None


def test_retry_after_http_date():
    future = datetime.now(UTC) + timedelta(seconds=120)
    val = retry_after_seconds(_resp({"Retry-After": format_datetime(future)}))
    assert val is not None and 100 <= val <= 130
