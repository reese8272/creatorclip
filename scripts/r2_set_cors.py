"""Set the R2 bucket CORS policy required for direct browser uploads (Issue 395).

The SPA PUTs multipart part bytes straight to the R2 S3 endpoint with presigned
URLs, so the bucket must allow cross-origin PUTs from the app origin AND expose
ETag — the JS client reads each part's ETag to assemble the completion manifest;
without ExposeHeaders the upload stalls at 100% with opaque CORS errors.

GET is deliberately absent: <video src> playback of presigned URLs is a no-CORS
request and needs no policy.

Reads R2_* from the environment (auto-loads ./.env like r2_inspect.py).

Usage:
    python3.12 scripts/r2_set_cors.py https://autoclip.studio
    python3.12 scripts/r2_set_cors.py https://autoclip.studio http://localhost:8000
"""

import json
import os
import sys

import boto3
from botocore.config import Config
from r2_inspect import _load_env  # sibling script; sys.path[0] is scripts/ when run directly

MAX_AGE_SECONDS = 3600  # cache preflights for an hour — parts reuse one preflight


def main() -> None:
    origins = sys.argv[1:]
    if not origins or any(not o.startswith(("http://", "https://")) for o in origins):
        print("Usage: python3.12 scripts/r2_set_cors.py <app-origin> [more-origins...]")
        print("       e.g. python3.12 scripts/r2_set_cors.py https://autoclip.studio")
        raise SystemExit(2)

    _load_env()
    account = os.environ["R2_ACCOUNT_ID"]
    bucket = os.environ["R2_BUCKET"]
    client = boto3.client(
        "s3",
        endpoint_url=f"https://{account}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
        config=Config(signature_version="s3v4", retries={"mode": "adaptive", "max_attempts": 5}),
    )

    policy = {
        "CORSRules": [
            {
                "AllowedOrigins": origins,
                "AllowedMethods": ["PUT"],
                "AllowedHeaders": ["Content-Type"],
                "ExposeHeaders": ["ETag"],
                "MaxAgeSeconds": MAX_AGE_SECONDS,
            }
        ]
    }
    client.put_bucket_cors(Bucket=bucket, CORSConfiguration=policy)
    print(f"CORS policy set on bucket {bucket} for origins: {', '.join(origins)}")

    echo = client.get_bucket_cors(Bucket=bucket)
    print("\nBucket now reports:")
    print(json.dumps(echo.get("CORSRules", []), indent=2))
    print("\nNote: R2 propagates CORS changes in ~30 seconds.")


if __name__ == "__main__":
    main()
