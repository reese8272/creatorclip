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
    _local_hosts = ("localhost", "127.0.0.1", "postgres", "db")
    if "sslmode=" not in url and not any(h in url for h in _local_hosts):
        url += ("&" if "?" in url else "?") + "sslmode=require"
    return url


def main() -> None:
    _load_env()
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        print(
            "DATABASE_URL not set. Add it to .env, or run against the live VM DB:\n"
            "  ssh creatorclip-vm 'cd /opt/autoclip && docker compose "
            "-f docker-compose.prod.yml exec -T app python3.12 scripts/clip_pipeline_state.py'"
        )
        sys.exit(1)
    creator = sys.argv[1] if len(sys.argv) > 1 else None

    with psycopg.connect(_normalize(raw), connect_timeout=15) as conn, conn.cursor() as cur:
        # Every tenant table is FORCE ROW LEVEL SECURITY and the app role has no
        # BYPASSRLS, so with `app.creator_id` unset these SELECTs return zero rows
        # and NO error — which this script used to report as "No videos found."
        # against a perfectly full database. `creators` is RLS-exempt, so it is the
        # bootstrap; every query below runs with the GUC set for one creator at a
        # time, INCLUDING the clip breakdown (leaving the GUC on whichever creator
        # happened to be last would silently hide every clip row).
        # See scripts/clip_audit.py.
        if creator:
            creator_ids = [creator]
        else:
            cur.execute("SELECT id FROM creators")
            creator_ids = [str(c[0]) for c in cur.fetchall()]

        videos: list = []
        clip_rows: dict = {}
        for cid in creator_ids:
            cur.execute("SELECT set_config('app.creator_id', %s, false)", (cid,))
            cur.execute(
                "SELECT id, youtube_video_id, ingest_status, failure_reason, created_at "
                "FROM videos ORDER BY created_at DESC LIMIT 50"
            )
            mine = cur.fetchall()
            if not mine:
                continue
            videos.extend(mine)
            # Clip breakdown per video, while this creator's GUC is still set.
            cur.execute(
                "SELECT video_id, render_status, COUNT(*), "
                "COUNT(render_uri) FILTER (WHERE render_uri IS NOT NULL) "
                "FROM clips WHERE video_id = ANY(%s) GROUP BY video_id, render_status",
                ([v[0] for v in mine],),
            )
            for vid, status, n, with_uri in cur.fetchall():
                clip_rows.setdefault(vid, []).append((str(status), n, with_uri))

        videos.sort(key=lambda v: v[4], reverse=True)
        videos = videos[: 50 if creator else 20]

        if not videos:
            print(
                "No videos found. (If this is unexpected: every tenant table is FORCE RLS "
                "and the app role has no BYPASSRLS, so an unset app.creator_id GUC yields "
                "zero rows with no error.)"
            )
            return

        print(f"{'video':>12}  {'yt_id':<14} {'ingest':<8}  clips (by render_status)")
        print("-" * 78)
        for vid, yt, ingest, reason, _created in videos:
            short = str(vid)[:8]
            cl = clip_rows.get(vid, [])
            if cl:
                summary = ", ".join(f"{st}={n}" + (f"(uri:{u})" if u else "") for st, n, u in cl)
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
