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

    # Issue 540: seed a local pg16 for a real-backend UI/Playwright audit —
    # everything the default profile seeds, PLUS clips/transcripts/edit docs/
    # a recap bundle/notifications so every authed route in frontend/src/App.tsx
    # renders non-empty:
    python3 tests/perf/seed_staging.py --profile ui postgresql://creatorclip:dev_password@localhost:5432/creatorclip_e2e

The script prints the seeded CC_CREATOR_ID — copy it into your Locust env.
Re-running is safe: it upserts on google_sub so the same creator UUID is
preserved across re-seeds. The ``ui`` profile is fully ON CONFLICT-idempotent
too (deterministic uuid5 ids derived from the fixed creator UUID) — safe to
re-run without dropping the database.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta

import psycopg

_CREATOR_ID = uuid.UUID("00000000-1111-2222-3333-444444444444")
_GOOGLE_SUB = "staging-load-test-creator"
_CHANNEL_ID = "UC_staging_load_test"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "database_url",
        nargs="?",
        default=None,
        help="Postgres DSN; falls back to $DATABASE_URL",
    )
    parser.add_argument(
        "--profile",
        choices=["default", "ui"],
        default="default",
        help="'default' (unchanged) seeds the load-test fixture only; "
        "'ui' additionally seeds clips/transcripts/edit-docs/recap/notifications "
        "so every route in frontend/src/App.tsx renders non-empty (Issue 540)",
    )
    return parser.parse_args(argv)


def _db_url(args: argparse.Namespace) -> str:
    if args.database_url:
        return args.database_url.replace("postgresql+psycopg://", "postgresql://")
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise SystemExit(
            "Pass DATABASE_URL as an argument or set the DATABASE_URL env var.\n"
            "Example: postgresql://creatorclip:secret@localhost:5433/creatorclip_staging"
        )
    # psycopg3 wants postgresql://, not the SQLAlchemy postgresql+psycopg:// form.
    return url.replace("postgresql+psycopg://", "postgresql://")


def _seed(conn: psycopg.Connection) -> list[str]:
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

    return video_ids


# ── UI profile (Issue 540) ──────────────────────────────────────────────────
# Deterministic uuid5 ids (derived from the fixed creator UUID) rather than the
# candidate-uuid4-then-resolve-by-natural-key dance `_seed` uses above: none of
# these rows have a natural key to resolve by, so ON CONFLICT (id) DO UPDATE is
# the idempotent primitive. `clips` additionally carries a DEFERRED unique
# constraint on (video_id, rank) that Postgres will not accept as an ON
# CONFLICT arbiter, which is the other reason the id must be the target.


def _uid(*parts: str) -> str:
    return str(uuid.uuid5(_CREATOR_ID, ":".join(parts)))


def _seed_ui(conn: psycopg.Connection, video_ids: list[str]) -> None:
    now = datetime.now(UTC)
    video_a, video_b = video_ids[0], video_ids[1]  # long videos (kind='long')

    # ── Clips — 2 videos, all three triage piles, all three render states ──
    clip_specs = [
        # (key, video_id, rank, triage, render_status, score, has_reframe)
        ("a1", video_a, 1, "pending", "pending", 0.81, False),
        ("a2", video_a, 2, "kept", "done", 0.76, True),
        ("a3", video_a, 3, "dropped", "failed", 0.42, False),
        ("b1", video_b, 1, "pending", "done", 0.79, False),
        ("b2", video_b, 2, "kept", "done", 0.71, False),
        ("b3", video_b, 3, "dropped", "pending", 0.38, False),
    ]
    # Real named principles (docs/CLIPPING_PRINCIPLES.md) — routers/clips.py's
    # ReviewClip.principle/.reasoning are read straight off signals_jsonb, so an
    # invented slug here would leave the Review UI showing a principle name that
    # cites nothing in the registry.
    _PRINCIPLE = "Clip the setup, not the aftermath"
    _REASONING = "Seed fixture — opens on the setup beat (setup_start_s), not the reaction peak."

    clip_ids: dict[str, str] = {}
    for key, vid, rank, triage, render_status, score, has_reframe in clip_specs:
        clip_id = _uid("clip", key)
        clip_ids[key] = clip_id
        reframe = (
            json.dumps(
                {
                    "version": 1,
                    "fps": 30,
                    "boxes": [{"t": 0.0, "x": 0.2, "y": 0.1, "w": 0.6, "h": 0.8}],
                }
            )
            if has_reframe
            else None
        )
        poster = f"https://staging.local/posters/{clip_id}.jpg" if render_status == "done" else None
        render_uri = (
            f"https://staging.local/renders/{clip_id}.mp4" if render_status == "done" else None
        )
        conn.execute(
            """
            INSERT INTO clips (id, video_id, creator_id, setup_start_s, start_s, end_s,
                               peak_s, score, blended_score, dna_match, signals_jsonb,
                               format, render_uri, render_status, rank, triage,
                               poster_uri, reframe_track_jsonb, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    'short', %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE
                SET render_status = EXCLUDED.render_status,
                    triage = EXCLUDED.triage,
                    score = EXCLUDED.score,
                    rank = EXCLUDED.rank
            """,
            (
                clip_id,
                vid,
                str(_CREATOR_ID),
                5.0,
                10.0,
                70.0,
                18.0,
                score,
                score,
                score - 0.05,
                json.dumps({"principle": _PRINCIPLE, "reasoning": _REASONING, "origin": "engine"}),
                render_uri,
                render_status,
                rank,
                triage,
                poster,
                reframe,
                now,
            ),
        )

    # ── Transcript (video_a) — segments + word-level timing ────────────────
    conn.execute(
        """
        INSERT INTO transcripts (video_id, source, segments_jsonb)
        VALUES (%s, 'deepgram', %s)
        ON CONFLICT (video_id) DO UPDATE SET segments_jsonb = EXCLUDED.segments_jsonb
        """,
        (
            video_a,
            json.dumps(
                {
                    "segments": [
                        {
                            "start": 0.0,
                            "end": 4.2,
                            "text": "Welcome back to the stream, let's get into it.",
                            "words": [
                                {"word": "Welcome", "start": 0.0, "end": 0.4},
                                {"word": "back", "start": 0.4, "end": 0.7},
                            ],
                        },
                        {
                            "start": 4.2,
                            "end": 9.5,
                            "text": "Today we're going to build the whole pipeline live.",
                            "words": [
                                {"word": "Today", "start": 4.2, "end": 4.5},
                                {"word": "we're", "start": 4.5, "end": 4.7},
                            ],
                        },
                    ]
                }
            ),
        ),
    )

    # ── Edit document (Editor route) — for the kept, rendered clip a2 ──────
    conn.execute(
        """
        INSERT INTO clip_edit_documents (id, clip_id, creator_id, doc, revision, created_at, updated_at)
        VALUES (%s, %s, %s, %s, 0, %s, %s)
        ON CONFLICT (clip_id) DO UPDATE SET doc = EXCLUDED.doc
        """,
        (
            _uid("editdoc", "a2"),
            clip_ids["a2"],
            str(_CREATOR_ID),
            json.dumps(
                {
                    "version": 1,
                    "cuts": [{"id": "cut-1", "start_s": 12.4, "end_s": 15.08}],
                    "last_applied_at": None,
                }
            ),
            now,
            now,
        ),
    )

    # ── Recap bundle (Summary) — the L32 surface, on video_a ───────────────
    conn.execute(
        """
        INSERT INTO summaries (id, creator_id, video_id, target_duration_s, segments,
                               dna_version, render_status, status, created_at)
        VALUES (%s, %s, %s, 480, %s, 1, 'done', 'ready', %s)
        ON CONFLICT (id) DO UPDATE SET segments = EXCLUDED.segments, status = EXCLUDED.status
        """,
        (
            _uid("summary", video_a),
            str(_CREATOR_ID),
            video_a,
            json.dumps(
                [
                    {
                        "start_s": 60.0,
                        "end_s": 130.0,
                        "score": 0.82,
                        "principle": "Hook in the first 3 seconds",
                        "rationale": "Strong reaction spike at stream open.",
                    },
                    {
                        "start_s": 900.0,
                        "end_s": 965.0,
                        "score": 0.74,
                        "principle": "Tension and release",
                        "rationale": "Chat activity peak on the reveal.",
                    },
                ]
            ),
            now,
        ),
    )

    # ── Notifications (in-app notification center) ─────────────────────────
    conn.execute(
        """
        INSERT INTO notifications (id, creator_id, kind, title, body, link_url,
                                   seen_at, dismissed_at, created_at)
        VALUES (%s, %s, 'clips_ready', 'Your clips are ready',
                'AutoClip finished generating clips for Staging video #1.',
                %s, NULL, NULL, %s)
        ON CONFLICT (id) DO UPDATE SET seen_at = NULL, dismissed_at = NULL
        """,
        (_uid("notif", "clips-ready"), str(_CREATOR_ID), f"/app/review?video_id={video_a}", now),
    )
    conn.execute(
        """
        INSERT INTO notifications (id, creator_id, kind, title, body, link_url,
                                   seen_at, dismissed_at, created_at)
        VALUES (%s, %s, 'recap_ready', 'Your recap is ready',
                'The livestream recap for Staging video #1 finished rendering.',
                %s, %s, NULL, %s)
        ON CONFLICT (id) DO UPDATE SET seen_at = EXCLUDED.seen_at
        """,
        (_uid("notif", "recap-ready"), str(_CREATOR_ID), f"/app/video/{video_a}/recap", now, now),
    )

    # ── notification_deliveries — the idempotency ledger, not the UI table ──
    conn.execute(
        """
        INSERT INTO notification_deliveries (id, creator_id, event_type, entity_id,
                                              channel, dedupe_key, provider_message_id,
                                              handled_by, status, created_at)
        VALUES (%s, %s, 'clips_ready', %s, 'email', %s, 'msg_staging_seed_1', 'resend', 'sent', %s)
        ON CONFLICT (dedupe_key) DO UPDATE SET status = EXCLUDED.status
        """,
        (_uid("delivery", "sent"), str(_CREATOR_ID), video_a, _uid("dedupe", "sent"), now),
    )
    conn.execute(
        """
        INSERT INTO notification_deliveries (id, creator_id, event_type, entity_id,
                                              channel, dedupe_key, status, created_at)
        VALUES (%s, %s, 'recap_ready', %s, 'inapp', %s, 'pending', %s)
        ON CONFLICT (dedupe_key) DO UPDATE SET status = EXCLUDED.status
        """,
        (_uid("delivery", "pending"), str(_CREATOR_ID), video_a, _uid("dedupe", "pending"), now),
    )

    # ── ClipOutcome + ClipPublication(done) — proof-of-lift needs the join ──
    for key in ("a2", "b2"):
        clip_id = clip_ids[key]
        conn.execute(
            """
            INSERT INTO clip_outcomes (clip_id, published_youtube_id, views, retention,
                                       performed_well, fetched_at, final)
            VALUES (%s, %s, %s, %s, %s, %s, true)
            ON CONFLICT (clip_id) DO UPDATE SET performed_well = EXCLUDED.performed_well
            """,
            (clip_id, f"yt_seed_{key}", 5000, 0.55, key == "a2", now),
        )
        conn.execute(
            """
            INSERT INTO clip_publications (id, clip_id, creator_id, youtube_video_id, status,
                                           platform, confirmed_at, created_at, updated_at)
            VALUES (%s, %s, %s, %s, 'done', 'youtube', %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status
            """,
            (
                _uid("publication", key),
                clip_id,
                str(_CREATOR_ID),
                f"yt_seed_{key}",
                now,
                now,
                now,
            ),
        )

    # ── Saved CreatorInsight — /creators/me/insights/saved ──────────────────
    conn.execute(
        """
        INSERT INTO creator_insights (id, creator_id, video_id, insight_type, title,
                                      content, dna_version, is_saved, created_at)
        VALUES (%s, %s, %s, 'performer_analysis', 'Cold opens are outperforming',
                'Clips that open on the reaction beat retain 12%% longer than average.',
                1, true, %s)
        ON CONFLICT (id) DO UPDATE SET is_saved = EXCLUDED.is_saved
        """,
        (_uid("insight", "saved-1"), str(_CREATOR_ID), video_a, now),
    )

    # ── Retention curve points (Analysis route) — 2 videos ──────────────────
    for vid in (video_a, video_b):
        for i, ratio in enumerate([1.0, 0.82, 0.68, 0.55, 0.41]):
            conn.execute(
                """
                INSERT INTO retention_curves (id, video_id, timestamp_s, audience_watch_ratio,
                                              relative_retention_performance, is_rewatch_spike)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (video_id, timestamp_s) DO UPDATE
                    SET audience_watch_ratio = EXCLUDED.audience_watch_ratio
                """,
                (
                    _uid("retention", vid, str(i)),
                    vid,
                    float(i * 60),
                    ratio,
                    ratio - 0.5,
                    i == 1,
                ),
            )


def main() -> None:
    args = _parse_args(sys.argv[1:])
    url = _db_url(args)
    print(f"Connecting to: {url.split('@')[-1]}")  # host/db only — no credentials in output
    with psycopg.connect(url, autocommit=False) as conn:
        # The `creatorclip` app role has no BYPASSRLS (by design — see db.py);
        # every tenant_isolation policy gates on `app.creator_id`. The app sets
        # this per-request via SET LOCAL; this script is not a request, so it
        # must set it itself. Every row below belongs to the fixed creator, so
        # one session-scoped set_config for the whole seed transaction covers
        # every table's policy (direct creator_id column or a video_id/clip_id
        # subquery back to it).
        conn.execute("SELECT set_config('app.creator_id', %s, false)", (str(_CREATOR_ID),))
        video_ids = _seed(conn)
        if args.profile == "ui":
            _seed_ui(conn, video_ids)
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
