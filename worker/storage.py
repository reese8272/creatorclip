"""
Storage adapter: local disk (dev) or Cloudflare R2 / S3 (prod).

Callers always work with local file paths and opaque URIs.
Only this module imports boto3.

Sync vs. async surface (Issue 38 Wave 1): boto3 has no native async client,
so the sync functions below are wrapped via `asyncio.to_thread` in the
``a*`` async counterparts. Async code paths (Celery tasks, FastAPI handlers)
should prefer the async variants so the event loop is not blocked for the
duration of the multi-second upload / download / delete round-trip.
"""

import asyncio
import shutil
import tempfile
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config

from config import settings

_R2 = None  # lazy singleton; populated on first R2 call via _r2()


def _r2() -> Any:  # boto3 clients are runtime-generated; no stubs pinned
    global _R2
    if _R2 is None:
        _R2 = boto3.client(
            "s3",
            endpoint_url=f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
            aws_access_key_id=settings.R2_ACCESS_KEY_ID,
            aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
            region_name="auto",
            config=Config(
                signature_version="s3v4",
                retries={"mode": "adaptive", "max_attempts": 5},
                connect_timeout=10,
                read_timeout=60,
            ),
        )
    return _R2


def _local_root() -> Path:
    # expanduser().resolve() converts relative paths (e.g. "./media") to absolute
    # paths before use. This prevents the path from shifting if the worker's cwd
    # changes between calls, and makes the configured value deterministic across
    # all callers. The production validator in config.py rejects relative values
    # in ENV=production so this is defence-in-depth for dev.
    d = Path(settings.LOCAL_MEDIA_DIR).expanduser().resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


def upload_file(src: str | Path, key: str) -> str:
    """Store src at key, return an opaque URI."""
    if settings.STORAGE_BACKEND == "r2":
        _r2().upload_file(str(src), settings.R2_BUCKET, key)
        return f"s3://{settings.R2_BUCKET}/{key}"
    dest = _local_root() / key
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(src), str(dest))
    return str(dest)


def delete_file(uri: str) -> None:
    if uri.startswith("s3://"):
        parts = uri[5:].split("/", 1)
        _r2().delete_object(Bucket=parts[0], Key=parts[1])
    else:
        p = Path(uri)
        if p.exists():
            p.unlink()


def delete_prefix(prefix: str) -> int:
    """Delete all objects whose key starts with prefix. Returns count deleted."""
    if settings.STORAGE_BACKEND == "r2":
        bucket = settings.R2_BUCKET
        deleted = 0
        paginator = _r2().get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            objects = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
            if objects:
                _r2().delete_objects(Bucket=bucket, Delete={"Objects": objects})
                deleted += len(objects)
        return deleted
    else:
        root = _local_root() / prefix
        if root.exists():
            import shutil as _shutil

            count = sum(1 for _ in root.rglob("*") if _.is_file())
            _shutil.rmtree(root)
            return count
        return 0


def presigned_download_url(
    uri: str, *, filename: str, disposition: str = "attachment", expires_s: int = 300
) -> str | None:
    """Return a short-lived presigned GET URL for an ``s3://`` object, carrying a
    ``Content-Disposition`` (``attachment`` forces a download, ``inline`` allows
    in-browser playback) and a humanized ``filename``.

    Returns ``None`` for non-``s3://`` (local-disk dev) URIs — callers serve those
    straight from disk. Presigned URLs are bearer tokens, so the expiry is kept
    short (default 5 min). ``generate_presigned_url`` only signs locally; it makes
    no network call, so it is safe to invoke from an async request handler.
    """
    if not uri.startswith("s3://"):
        return None
    bucket, key = uri[5:].split("/", 1)
    return _r2().generate_presigned_url(
        "get_object",
        Params={
            "Bucket": bucket,
            "Key": key,
            "ResponseContentDisposition": f'{disposition}; filename="{filename}"',
        },
        ExpiresIn=expires_s,
    )


@contextmanager
def local_path(uri: str) -> Generator[Path, None, None]:
    """Yield a local Path; downloads to a temp file first if the URI is remote."""
    if uri.startswith("s3://"):
        parts = uri[5:].split("/", 1)
        suffix = Path(parts[1]).suffix
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            _r2().download_file(parts[0], parts[1], str(tmp_path))
            yield tmp_path
        finally:
            tmp_path.unlink(missing_ok=True)
    else:
        yield Path(uri)


# ── Async wrappers (Issue 38 Wave 1) ──────────────────────────────────────────
#
# boto3 has no native async client. These wrappers run the sync boto3 calls in
# a thread pool via asyncio.to_thread so async callers (Celery task bodies,
# FastAPI handlers) do not block the event loop while a multi-second upload /
# download / delete is in flight.


async def aupload_file(src: str | Path, key: str) -> str:
    return await asyncio.to_thread(upload_file, src, key)


async def adelete_file(uri: str) -> None:
    await asyncio.to_thread(delete_file, uri)


async def adelete_prefix(prefix: str) -> int:
    return await asyncio.to_thread(delete_prefix, prefix)


@asynccontextmanager
async def alocal_path(uri: str) -> AsyncGenerator[Path, None]:
    """Async counterpart of `local_path` — the boto3 download is offloaded to a
    worker thread. For non-s3 URIs this is a thin async wrapper around yielding
    the existing path.
    """
    if uri.startswith("s3://"):
        parts = uri[5:].split("/", 1)
        suffix = Path(parts[1]).suffix
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            await asyncio.to_thread(_r2().download_file, parts[0], parts[1], str(tmp_path))
            yield tmp_path
        finally:
            tmp_path.unlink(missing_ok=True)
    else:
        yield Path(uri)
