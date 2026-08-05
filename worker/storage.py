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
from botocore.exceptions import ClientError

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


# Top-level key prefixes whose footprint the daily storage-gauge sweep reports
# (Issue 293). Fixed set — keeps the Prometheus `prefix` label low-cardinality.
STORAGE_GAUGE_PREFIXES: tuple[str, ...] = (
    "source/",
    "audio/",
    "clips/",
    "summaries/",
    "posters/",  # Issue 387 — poster frames, ~50 KB each.
    "peaks/",  # Issue 392 — waveform envelopes, ~90 KB gzipped for a 22-min source.
)


def measure_prefix(prefix: str) -> tuple[int, int]:
    """Return ``(total_bytes, object_count)`` for all objects under *prefix*.

    R2 backend: paginated ``list_objects_v2`` sweep (same idiom as
    ``delete_prefix``) summing ``Size``. Local backend: walk
    ``LOCAL_MEDIA_DIR/prefix`` on disk. May raise on client/IO errors — the
    gauge task (worker.tasks.collect_storage_gauges) catches per-prefix.
    """
    total_bytes = 0
    count = 0
    if settings.STORAGE_BACKEND == "r2":
        paginator = _r2().get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=settings.R2_BUCKET, Prefix=prefix):
            for obj in page.get("Contents", []):
                total_bytes += int(obj.get("Size", 0))
                count += 1
        return total_bytes, count
    root = _local_root() / prefix
    if not root.exists():
        return 0, 0
    for p in root.rglob("*"):
        if p.is_file():
            total_bytes += p.stat().st_size
            count += 1
    return total_bytes, count


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


def read_bytes(uri: str, *, max_bytes: int = 2_000_000) -> bytes | None:
    """Read a small object into memory, or None when it is absent.

    For POSTER FRAMES ONLY (Issue 387) — deliberately not a general media reader.
    We refuse to proxy clip bytes because clips are 10-50 MB and need Range
    support; a poster is ~50 KB, which is what makes the byte-proxy endpoint a
    principled inconsistency rather than an oversight. `max_bytes` is
    defence-in-depth: we wrote the file at a known size, so anything larger means
    something is wrong.
    """
    if uri.startswith("s3://"):
        _, _, rest = uri.partition("s3://")
        bucket, _, key = rest.partition("/")
        try:
            obj = _r2().get_object(Bucket=bucket, Key=key)
        except Exception:
            return None
        with obj["Body"] as body:
            return body.read(max_bytes)
    path = Path(uri)
    if not path.is_file() or path.stat().st_size > max_bytes:
        return None
    return path.read_bytes()


async def aupload_file(src: str | Path, key: str) -> str:
    return await asyncio.to_thread(upload_file, src, key)


async def aread_bytes(uri: str, *, max_bytes: int = 2_000_000) -> bytes | None:
    return await asyncio.to_thread(read_bytes, uri, max_bytes=max_bytes)


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


# ── Presigned multipart upload (Issue 395) ────────────────────────────────────
#
# Browser-direct uploads: the API mints presigned part URLs and the browser PUTs
# straight to R2, so these helpers exist only for the r2 backend. Callers gate on
# STORAGE_BACKEND before reaching them; the RuntimeError is a programming-error
# backstop, not a user-facing path. boto3/botocore error types stay confined to
# this module — callers see MultipartUploadNotFound / StorageError instead.


class MultipartUploadNotFound(Exception):
    """The uploadId no longer exists (completed, aborted, or auto-expired)."""


class StorageError(Exception):
    """A storage-backend call failed for a reason other than a missing upload."""


def _require_r2(op: str) -> None:
    if settings.STORAGE_BACKEND != "r2":
        raise RuntimeError(f"{op} requires STORAGE_BACKEND=r2")


def _translate_client_error(exc: ClientError) -> Exception:
    code = exc.response.get("Error", {}).get("Code", "")
    if code == "NoSuchUpload":
        return MultipartUploadNotFound()
    return StorageError(code or "unknown")


def create_multipart_upload(key: str, content_type: str | None = None) -> str:
    """Start a multipart upload for key, returning the R2 UploadId."""
    _require_r2("create_multipart_upload")
    kwargs: dict[str, Any] = {"Bucket": settings.R2_BUCKET, "Key": key}
    if content_type:
        kwargs["ContentType"] = content_type
    try:
        return _r2().create_multipart_upload(**kwargs)["UploadId"]
    except ClientError as exc:
        raise _translate_client_error(exc) from exc


def presign_upload_part(key: str, upload_id: str, part_number: int, *, expires_s: int = 900) -> str:
    """Presigned PUT URL for one part. Signing is local-only (no network call),
    so this is safe to invoke directly from an async request handler — same
    property as `presigned_download_url`. No extra headers are signed: the
    browser sends the raw part body and nothing else.
    """
    _require_r2("presign_upload_part")
    return _r2().generate_presigned_url(
        "upload_part",
        Params={
            "Bucket": settings.R2_BUCKET,
            "Key": key,
            "UploadId": upload_id,
            "PartNumber": part_number,
        },
        ExpiresIn=expires_s,
    )


def list_upload_parts(key: str, upload_id: str) -> list[dict[str, Any]]:
    """All parts uploaded so far — the client's resume primitive. Paginates
    ListParts (R2 caps a page at 1000 parts; uploads may hold up to 10,000).
    """
    _require_r2("list_upload_parts")
    parts: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {
        "Bucket": settings.R2_BUCKET,
        "Key": key,
        "UploadId": upload_id,
    }
    while True:
        try:
            resp = _r2().list_parts(**kwargs)
        except ClientError as exc:
            raise _translate_client_error(exc) from exc
        for part in resp.get("Parts", []):
            parts.append(
                {"part_number": part["PartNumber"], "size": part["Size"], "etag": part["ETag"]}
            )
        if not resp.get("IsTruncated"):
            return parts
        kwargs["PartNumberMarker"] = resp["NextPartNumberMarker"]


def complete_multipart_upload(key: str, upload_id: str, parts: list[dict[str, Any]]) -> str:
    """Assemble the parts into the final object; returns the canonical URI.

    Parts are sorted by part_number here — S3 rejects out-of-order manifests and
    the client's retry order is not guaranteed to be ascending.
    """
    _require_r2("complete_multipart_upload")
    manifest = {
        "Parts": [
            {"PartNumber": p["part_number"], "ETag": p["etag"]}
            for p in sorted(parts, key=lambda p: p["part_number"])
        ]
    }
    try:
        _r2().complete_multipart_upload(
            Bucket=settings.R2_BUCKET, Key=key, UploadId=upload_id, MultipartUpload=manifest
        )
    except ClientError as exc:
        raise _translate_client_error(exc) from exc
    return f"s3://{settings.R2_BUCKET}/{key}"


def abort_multipart_upload(key: str, upload_id: str) -> None:
    _require_r2("abort_multipart_upload")
    try:
        _r2().abort_multipart_upload(Bucket=settings.R2_BUCKET, Key=key, UploadId=upload_id)
    except ClientError as exc:
        raise _translate_client_error(exc) from exc


def head_object(key: str) -> dict[str, Any] | None:
    """Size + etag of an object, or None when it does not exist."""
    _require_r2("head_object")
    try:
        resp = _r2().head_object(Bucket=settings.R2_BUCKET, Key=key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return None
        raise _translate_client_error(exc) from exc
    return {"size": resp["ContentLength"], "etag": resp["ETag"]}


async def acreate_multipart_upload(key: str, content_type: str | None = None) -> str:
    return await asyncio.to_thread(create_multipart_upload, key, content_type)


async def alist_upload_parts(key: str, upload_id: str) -> list[dict[str, Any]]:
    return await asyncio.to_thread(list_upload_parts, key, upload_id)


async def acomplete_multipart_upload(key: str, upload_id: str, parts: list[dict[str, Any]]) -> str:
    return await asyncio.to_thread(complete_multipart_upload, key, upload_id, parts)


async def aabort_multipart_upload(key: str, upload_id: str) -> None:
    await asyncio.to_thread(abort_multipart_upload, key, upload_id)


async def ahead_object(key: str) -> dict[str, Any] | None:
    return await asyncio.to_thread(head_object, key)
