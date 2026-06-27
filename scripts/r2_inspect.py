"""Ad-hoc R2 bucket inspector — read-only.

Reads R2_* + R2_BUCKET from the environment (load .env first) and reports what the
live app has written: object count, total size, a breakdown by top-level key prefix,
and the most recently modified objects. Purely diagnostic; performs no writes.

Usage:
    python3.12 scripts/r2_inspect.py        # auto-loads ./.env (handles "KEY = value")
"""

import os
from collections import defaultdict
from pathlib import Path

import boto3
from botocore.config import Config


def _load_env(path: str = ".env") -> None:
    """Minimal .env loader tolerant of `KEY = value` spacing. Does not overwrite
    variables already present in the real environment."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = val


def _human(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if f < 1024 or unit == "TB":
            return f"{f:.1f}{unit}"
        f /= 1024
    return f"{f:.1f}TB"


def main() -> None:
    _load_env()
    account = os.environ["R2_ACCOUNT_ID"]
    bucket = os.environ["R2_BUCKET"]
    client = boto3.client(
        "s3",
        endpoint_url=f"https://{account}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        config=Config(retries={"mode": "adaptive", "max_attempts": 5}),
    )

    print(f"Bucket: {bucket}\n")

    total_count = 0
    total_size = 0
    by_prefix: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # prefix -> [count, bytes]
    recent: list[tuple] = []

    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            key, size, mtime = obj["Key"], obj["Size"], obj["LastModified"]
            total_count += 1
            total_size += size
            top = key.split("/", 1)[0] if "/" in key else "(root)"
            by_prefix[top][0] += 1
            by_prefix[top][1] += size
            recent.append((mtime, size, key))

    print(f"Total objects: {total_count}")
    print(f"Total size:    {_human(total_size)}\n")

    if total_count == 0:
        print("Bucket is EMPTY — no media has been persisted yet.")
        return

    print("By top-level prefix:")
    for prefix, (count, size) in sorted(by_prefix.items(), key=lambda kv: -kv[1][1]):
        print(f"  {prefix:<28} {count:>5} objs  {_human(size):>10}")

    print("\n20 most recently modified:")
    recent.sort(reverse=True)
    for mtime, size, key in recent[:20]:
        print(f"  {mtime:%Y-%m-%d %H:%M:%S}  {_human(size):>9}  {key}")


if __name__ == "__main__":
    main()
