"""Unit tests for the daily storage-footprint gauge sweep (Issue 293).

Mocks the boto3 client at the worker.storage._r2 boundary — no network, no Docker.
Covers: per-prefix byte/object summing across paginated pages, the local-disk
backend walk, the never-raises posture on client errors, and the clean skip when
R2 is unconfigured.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError
from prometheus_client import REGISTRY

from config import settings
from worker.tasks import collect_storage_gauges


def _gauge(name: str, prefix: str) -> float | None:
    return REGISTRY.get_sample_value(name, {"prefix": prefix})


def _paginating_client(pages_by_prefix: dict[str, list[dict]]) -> MagicMock:
    client = MagicMock()
    paginator = MagicMock()
    paginator.paginate.side_effect = lambda Bucket, Prefix: iter(pages_by_prefix.get(Prefix, [{}]))
    client.get_paginator.return_value = paginator
    return client


def test_r2_sweep_sums_bytes_and_objects_per_prefix(monkeypatch) -> None:
    """Sizes are summed across pages; counts match; each prefix gets its own label."""
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "r2")
    monkeypatch.setattr(settings, "R2_ACCOUNT_ID", "acct")
    monkeypatch.setattr(settings, "R2_BUCKET", "bkt")
    client = _paginating_client(
        {
            "source/": [
                {"Contents": [{"Key": "source/a.mp4", "Size": 100}]},
                {"Contents": [{"Key": "source/b.mp4", "Size": 50}]},  # second page
            ],
            "clips/": [{"Contents": [{"Key": "clips/c.mp4", "Size": 7}]}],
            # audio/ and summaries/ return a page with no Contents (empty prefix)
        }
    )
    with patch("worker.storage._r2", return_value=client):
        collect_storage_gauges()

    assert _gauge("r2_bytes_stored", "source") == 150
    assert _gauge("r2_objects", "source") == 2
    assert _gauge("r2_bytes_stored", "clips") == 7
    assert _gauge("r2_objects", "clips") == 1
    assert _gauge("r2_bytes_stored", "audio") == 0
    assert _gauge("r2_objects", "summaries") == 0


def test_r2_sweep_never_raises_on_client_error(monkeypatch) -> None:
    """A ClientError on the list call is logged and swallowed — Beat must not see it."""
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "r2")
    monkeypatch.setattr(settings, "R2_ACCOUNT_ID", "acct")
    monkeypatch.setattr(settings, "R2_BUCKET", "bkt")
    client = MagicMock()
    client.get_paginator.return_value.paginate.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "nope"}}, "ListObjectsV2"
    )
    with patch("worker.storage._r2", return_value=client):
        collect_storage_gauges()  # must not raise


def test_skips_cleanly_when_r2_unconfigured(monkeypatch) -> None:
    """Empty R2 credentials → no client is ever constructed."""
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "r2")
    monkeypatch.setattr(settings, "R2_ACCOUNT_ID", "")
    monkeypatch.setattr(settings, "R2_BUCKET", "")
    with patch("worker.storage._r2") as mock_r2:
        collect_storage_gauges()
    mock_r2.assert_not_called()


def test_local_backend_walks_media_dir(monkeypatch, tmp_path: Path) -> None:
    """STORAGE_BACKEND=local fills the same gauges by walking LOCAL_MEDIA_DIR."""
    (tmp_path / "clips").mkdir()
    (tmp_path / "clips" / "a.mp4").write_bytes(b"x" * 10)
    (tmp_path / "clips" / "b.mp4").write_bytes(b"x" * 5)
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "LOCAL_MEDIA_DIR", str(tmp_path))

    collect_storage_gauges()

    assert _gauge("r2_bytes_stored", "clips") == 15
    assert _gauge("r2_objects", "clips") == 2
    assert _gauge("r2_objects", "source") == 0  # missing dir → zero, not error
