"""
Storage adapter: local disk (dev) or Cloudflare R2 / S3 (prod).

Callers always work with local file paths and opaque URIs.
Only this module imports boto3.
"""

import shutil
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from config import settings


def _r2_client():
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
    )


def _local_root() -> Path:
    d = Path(settings.LOCAL_MEDIA_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d


def upload_file(src: str | Path, key: str) -> str:
    """Store src at key, return an opaque URI."""
    if settings.STORAGE_BACKEND == "r2":
        _r2_client().upload_file(str(src), settings.R2_BUCKET, key)
        return f"s3://{settings.R2_BUCKET}/{key}"
    dest = _local_root() / key
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(src), str(dest))
    return str(dest)


def delete_file(uri: str) -> None:
    if uri.startswith("s3://"):
        parts = uri[5:].split("/", 1)
        _r2_client().delete_object(Bucket=parts[0], Key=parts[1])
    else:
        p = Path(uri)
        if p.exists():
            p.unlink()


def delete_prefix(prefix: str) -> int:
    """Delete all objects whose key starts with prefix. Returns count deleted."""
    if settings.STORAGE_BACKEND == "r2":
        client = _r2_client()
        bucket = settings.R2_BUCKET
        deleted = 0
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            objects = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
            if objects:
                client.delete_objects(Bucket=bucket, Delete={"Objects": objects})
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


@contextmanager
def local_path(uri: str) -> Generator[Path, None, None]:
    """Yield a local Path; downloads to a temp file first if the URI is remote."""
    if uri.startswith("s3://"):
        parts = uri[5:].split("/", 1)
        suffix = Path(parts[1]).suffix
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            _r2_client().download_file(parts[0], parts[1], str(tmp_path))
            yield tmp_path
        finally:
            tmp_path.unlink(missing_ok=True)
    else:
        yield Path(uri)
