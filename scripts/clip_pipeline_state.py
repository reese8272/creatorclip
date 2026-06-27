"""Read-only pipeline-state diagnostic — answers "why are there no clips?".

Reads DATABASE_URL from the environment (loads .env first, tolerating the
``KEY = value`` spacing used in this repo's .env) and prints, for the most
recent videos: ingest status + failure_reason, and the clip rows grouped by
render_status (with render_uri presence). Purely SELECT — no writes.

Usage:
    python3.12 scripts/clip_pipeline_state.py            # 20 most recent videos
    python3.12 scripts/clip_pipeline_state.py <creator_id>
"""

import os
import sys
from pathlib import Path

import psycopg


def _load_env(path: str = ".env") -> None:
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


def _normalize(url: str) -> str:
    """psycopg wants a plain postgresql:// URL; strip any SQLAlchemy driver
    suffix and add sslmode=require for non-local hosts (managed Postgres needs it)."""
    for suffix in ("+asyncpg", "+psycopg", "+psycopg2"):
        url = url.replace(suffix, "")
    if "sslmode=" not in url and "localhost" not in url and "127.0.0.1" not in url:
        url += ("&" if "?" in url else "?") + "sslmode=require"
    return url


def main() -> None:
    _load_env()
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        print("DATABASE_URL not set. Add it to .env, or run against the live VM DB:\n"
              "  ssh creatorclip-vm 'cd /opt/autoclip && docker compose "
              "-f docker-compose.prod.yml exec -T app python3.12 scripts/clip_pipeline_state.py'")
        sys.exit(1)
    creator = sys.argv[1] if len(sys.argv) > 1 else None

    with psycopg.connect(_normalize(raw), connect_timeout=15) as conn, conn.cursor() as cur:
        if creator:
            cur.execute(
                "SELECT id, youtube_video_id, ingest_status, failure_reason, created_at "
                "FROM videos WHERE creator_id = %s ORDER BY created_at DESC LIMIT 50",
                (creator,),
            )
        else:
            cur.execute(
                "SELECT id, youtube_video_id, ingest_status, failure_reason, created_at "
                "FROM videos ORDER BY created_at DESC LIMIT 20"
            )
        videos = cur.fetchall()

        if not videos:
            print("No videos found.")
            return

        # Clip breakdown per video: render_status counts + render_uri presence.
        vids = [v[0] for v in videos]
        cur.execute(
            "SELECT video_id, render_status, COUNT(*), "
            "COUNT(render_uri) FILTER (WHERE render_uri IS NOT NULL) "
            "FROM clips WHERE video_id = ANY(%s) GROUP BY video_id, render_status",
            (vids,),
        )
        clip_rows: dict = {}
        for vid, status, n, with_uri in cur.fetchall():
            clip_rows.setdefault(vid, []).append((str(status), n, with_uri))

        print(f"{'video':>12}  {'yt_id':<14} {'ingest':<8}  clips (by render_status)")
        print("-" * 78)
        for vid, yt, ingest, reason, created in videos:
            short = str(vid)[:8]
            cl = clip_rows.get(vid, [])
            if cl:
                summary = ", ".join(
                    f"{st}={n}" + (f"(uri:{u})" if u else "") for st, n, u in cl
                )
            else:
                summary = "NO CLIP ROWS"
            ing = ingest.value if hasattr(ingest, "value") else str(ingest)
            print(f"{short:>12}  {str(yt or '-'):<14} {ing:<8}  {summary}")
            if reason:
                print(f"               └─ failure_reason: {reason}")

        print("\nRead: ingest=done + NO CLIP ROWS → clip generation never produced candidates.")
        print("      clips with render_status=pending → render was never triggered.")
        print("      render_status=failed → render ran and failed (check worker logs).")


if __name__ == "__main__":
    main()
