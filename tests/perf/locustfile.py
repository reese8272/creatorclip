"""Load-test scaffold for CreatorClip — concurrency evidence for the assessment.

This produces the one thing reading the code cannot: real p95/p99 latency and
error behavior under hundreds of concurrent creators. It exercises the hot,
authenticated READ paths (the ones that hit Postgres/pgvector + Redis on every
request) plus a light feedback write. Heavy async jobs (clip generate/render)
are intentionally NOT hammered here — they are Celery jobs; load-test the queue
depth and worker latency separately.

Run against a STAGING stack, never production:

    pip install -r requirements-dev.txt
    export CC_BASE_URL=https://staging.example.com
    export CC_JWT_SECRET=<staging JWT_SECRET_KEY>
    export CC_CREATOR_ID=<a seeded staging creator uuid>
    locust -f tests/perf/locustfile.py --host "$CC_BASE_URL" \
           --users 300 --spawn-rate 20 --run-time 5m

Then read the percentile table. What to look for is documented in tests/perf/README.md.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from locust import HttpUser, between, task

SESSION_COOKIE = "cc_session"
_ALGORITHM = "HS256"


def _mint_token() -> str:
    """Mint a session JWT the same way auth.create_session_token does.

    Requires CC_JWT_SECRET (the staging JWT_SECRET_KEY) and CC_CREATOR_ID
    (a seeded creator). Kept in lock-step with auth.py — if the auth scheme
    changes, this must change too.
    """
    secret = os.environ["CC_JWT_SECRET"]
    creator_id = os.environ["CC_CREATOR_ID"]
    now = datetime.now(UTC)
    payload = {
        "sub": str(uuid.UUID(creator_id)),
        "iat": now,
        "exp": now + timedelta(hours=2),
    }
    return jwt.encode(payload, secret, algorithm=_ALGORITHM)


class CreatorUser(HttpUser):
    """Simulates one authenticated creator browsing their dashboard."""

    wait_time = between(1, 3)

    def on_start(self) -> None:
        self.client.cookies.set(SESSION_COOKIE, _mint_token())

    # Weights approximate a dashboard session: lots of reads, occasional write.
    @task(5)
    def list_videos(self) -> None:
        self.client.get("/videos", name="GET /videos")

    @task(4)
    def my_profile(self) -> None:
        self.client.get("/creators/me", name="GET /creators/me")

    @task(3)
    def my_dna(self) -> None:
        self.client.get("/creators/me/dna", name="GET /creators/me/dna")

    @task(2)
    def data_gate(self) -> None:
        self.client.get("/creators/me/data-gate", name="GET /creators/me/data-gate")

    @task(2)
    def balance(self) -> None:
        self.client.get("/billing/balance", name="GET /billing/balance")

    @task(1)
    def upload_intel(self) -> None:
        self.client.get("/creators/me/upload-intel", name="GET /creators/me/upload-intel")

    @task(1)
    def health(self) -> None:
        # Unauthenticated; surfaces DB/Redis saturation under load.
        self.client.get("/health", name="GET /health")
