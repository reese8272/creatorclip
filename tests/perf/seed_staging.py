"""Seed the staging database with one creator + realistic fixture data.

Run this AFTER `alembic upgrade head` on the staging stack. The script
inserts the minimum data needed for every Locust task to return realistic
payloads (non-empty SELECT results hide N+1 queries and serialization cost).

Usage:
    # From the prod VM, with the staging stack running:
    export DATABASE_URL="postgresql://creatorclip:${POSTGRES_PASSWORD}@localhost:5433/creatorclip_staging"
    python3 tests/perf/seed_staging.py

    # Or pass the URL directly:
    python3 tests/perf/seed_staging.py postgresql://creatorclip:secret@localhost:5433/creatorclip_staging

The script prints the seeded CC_CREATOR_ID — copy it into your Locust env.
Re-running is safe: it upserts on google_sub so the same creator UUID is
preserved across re-seeds.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta

import psycopg

_CREATOR_ID = uuid.UUID("00000000-1111-2222-3333-444444444444")
_GOOGLE_SUB = "staging-load-test-creator"
_CHANNEL_ID = "UC_staging_load_test"


def _db_url() -> str:
    if len(sys.argv) > 1:
        return sys.argv[1]
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise SystemExit(
            "Pass DATABASE_URL as an argument or set the DATABASE_URL env var.\n"
            "Example: postgresql://creatorclip:secret@localhost:5433/creatorclip_staging"
        )
    # psycopg3 wants postgresql://, not the SQLAlchemy postgresql+psycopg:// form.
    return url.replace("postgresql+psycopg://", "postgresql://")


def _seed(conn: psycopg.Connection) -> None:
    now = datetime.now(UTC)

    # ── Creator ────────────────────────────────────────────────────────────────
    conn.execute(
        """
        INSERT INTO creators (id, google_sub, channel_id, channel_title, email,
                              onboarding_state, minutes_balance, created_at)
        VALUES (%s, %s, %s, %s, %s, 'active', 500, %s)
        ON CONFLICT (google_sub) DO UPDATE
            SET channel_id = EXCLUDED.channel_id,
                onboarding_state = 'active',
                minutes_balance = 500
        """,
        (
            str(_CREATOR_ID),
            _GOOGLE_SUB,
            _CHANNEL_ID,
            "Staging Load-Test Channel",
            "staging@example.com",
            now,
        ),
    )

    # ── Videos + VideoMetrics ──────────────────────────────────────────────────
    # 12 videos: 8 longs, 4 shorts. Having non-empty tables prevents the
    # endpoints from short-circuiting before the real query logic runs.
    video_ids: list[str] = []
    for i in range(12):
        # Idempotency: the id must survive re-runs against the PERSISTENT staging
        # DB. A fresh uuid4 + ON CONFLICT (youtube_video_id) DO NOTHING left the
        # new id nonexistent on run #2, so the video_metrics insert FK-violated
        # (first staging-gate run after PR #49). Insert with a candidate id, then
        # resolve the REAL id by the natural key.
        vid_id = str(uuid.uuid4())
        kind = "long" if i < 8 else "short"
        duration = 720.0 + i * 60 if kind == "long" else 55.0 + i * 5
        published = now - timedelta(days=30 + i * 7)
        conn.execute(
            """
            INSERT INTO videos (id, creator_id, youtube_video_id, title, kind,
                                published_at, duration_s, ingest_status, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'done', %s)
            ON CONFLICT DO NOTHING
            """,
            (
                vid_id,
                str(_CREATOR_ID),
                f"yt_staging_{i:03d}",
                f"Staging video #{i + 1} — {kind}",
                kind,
                published,
                duration,
                now,
            ),
        )
        # Resolve the surviving row's id (pre-existing on re-runs).
        row = conn.execute(
            "SELECT id FROM videos WHERE youtube_video_id = %s",
            (f"yt_staging_{i:03d}",),
        ).fetchone()
        vid_id = str(row[0])
        video_ids.append(vid_id)
        # VideoMetrics so /videos and /creators/me/upload-intel return real numbers.
        conn.execute(
            """
            INSERT INTO video_metrics (video_id, views, watch_time_s,
                                       avg_view_duration_s, engagement_rate, fetched_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (
                vid_id,
                10_000 + i * 3_000,
                (10_000 + i * 3_000) * int(duration * 0.45),
                duration * 0.45,
                0.04 + i * 0.005,
                now,
            ),
        )

    # ── CreatorDna (confirmed) ─────────────────────────────────────────────────
    # GET /creators/me/dna only returns confirmed DNA.
    conn.execute(
        """
        INSERT INTO creator_dna (id, creator_id, version, brief_text, patterns_jsonb,
                                 top_video_ids_jsonb, bottom_video_ids_jsonb,
                                 optimal_clip_len_s, best_source_region,
                                 optimal_upload_gap_h, status, created_at)
        VALUES (%s, %s, 1,
                'Staging load-test DNA — auto-seeded.',
                %s, %s, %s,
                62.0, 'middle', 72.0, 'confirmed', %s)
        ON CONFLICT (creator_id, version) DO UPDATE
            SET status = 'confirmed'
        """,
        (
            str(uuid.uuid4()),
            str(_CREATOR_ID),
            json.dumps({"niche": ["staging", "load-test"], "tone": "educational"}),
            json.dumps(video_ids[:3]),
            json.dumps(video_ids[-3:]),
            now,
        ),
    )

    # ── CreatorIdentity ────────────────────────────────────────────────────────
    # Some endpoints query creator_identity for the niche/audience context.
    conn.execute(
        """
        INSERT INTO creator_identity (id, creator_id, version, niches,
                                      audience_summary, created_at)
        VALUES (%s, %s, 1, %s, %s, %s)
        ON CONFLICT (creator_id, version) DO NOTHING
        """,
        (
            str(uuid.uuid4()),
            str(_CREATOR_ID),
            json.dumps(["27"]),  # Education category
            "Developers aged 18–34 interested in AI tools.",
            now,
        ),
    )


def main() -> None:
    url = _db_url()
    print(f"Connecting to: {url.split('@')[-1]}")  # host/db only — no credentials in output
    with psycopg.connect(url, autocommit=False) as conn:
        _seed(conn)
        conn.commit()

    print()
    print("✓ Staging database seeded.")
    print()
    print("Export these vars before running Locust:")
    print("  export CC_BASE_URL=http://localhost:8001")
    print("  export CC_JWT_SECRET=<value of JWT_SECRET_KEY from .env>")
    print(f"  export CC_CREATOR_ID={_CREATOR_ID}")
    print()
    print("Then run:")
    print(
        "  locust -f tests/perf/locustfile.py --host $CC_BASE_URL"
        " --users 300 --spawn-rate 20 --run-time 5m --headless"
        " --csv docs/assessment/loadtest"
    )


if __name__ == "__main__":
    main()
