#!/usr/bin/env python3.12
"""Live-in-isolation smoke-test harness (Issue 341, Lane L22 — Live Smoke).

Exercises the REAL pipeline capabilities against a deployed target (Postgres +
R2 + ffmpeg + Anthropic), one capability at a time, isolated to a synthetic
``__smoke_canary__`` creator. This is the post-deploy "does it actually still
work?" check the mocked unit lane and the LLM-only ``llm_e2e.py`` cannot answer.

Design (see docs/DECISIONS.md, 2026-06-29 — flag-gated live canary):
  * The capabilities form a DAG: one indivisible upstream chain
    (ingest→transcribe→signals→generate_clips) and a fan-out of independent
    leaf operations (render, clean, title, caption, explain, publish). The leaf
    checks are isolated by operating on a PERSISTENT seeded canary fixture, so
    each runs standalone (`--only render`). This is the synthetic-monitoring /
    canary pattern (Datadog/Checkly/Martin Fowler), flag-selectable target.
  * Production safety (the standard for "unsafe synthetic transactions"):
      - publish to YouTube is DESTRUCTIVE → dry-run / pre-flight only by default;
        a real upload requires `--publish-live` AND `--target staging` (a
        throwaway channel), never prod.
      - metered LLM calls are gated behind `--with-llm` (off by default) so the
        free, deterministic core runs without token spend and without
        duplicating Issue 319's nightly.
      - every write is confined to the canary's namespace; `--teardown` purges it.

Usage:
    RUN_LIVE_SMOKE=1 python3.12 scripts/live_smoke.py --target prod --seed
    RUN_LIVE_SMOKE=1 python3.12 scripts/live_smoke.py --target prod
    RUN_LIVE_SMOKE=1 python3.12 scripts/live_smoke.py --only render
    RUN_LIVE_SMOKE=1 python3.12 scripts/live_smoke.py --with-llm --only title
    RUN_LIVE_SMOKE=1 python3.12 scripts/live_smoke.py --target staging --teardown

Environment:
    RUN_LIVE_SMOKE=1   — guard; the harness exits 0 immediately if unset.
    --target prod      — loads ./.env       (the deployed VM's connection set)
    --target staging   — loads ./.env.staging
  The selected env file must provide DATABASE_URL, R2_*/R2_BUCKET, and (for
  --with-llm) ANTHROPIC_API_KEY. Secrets are read, never logged.

Exit codes: 0 = all run checks passed (skips are not failures), 1 = a failure.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

# ── Make the repo root importable when run as `python scripts/live_smoke.py` ──
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── Env loading (mirrors scripts/clip_pipeline_state.py — tolerates "KEY = v") ─
def _load_env(path: str) -> bool:
    """Load a .env-style file into os.environ without overwriting real env.

    Returns True if the file existed and was read, False otherwise.
    """
    p = Path(path)
    if not p.exists():
        return False
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = val
    return True


def _normalize_pg(url: str) -> str:
    """psycopg wants a plain postgresql:// URL; strip any SQLAlchemy driver suffix.

    For non-local hosts add ``sslmode=prefer`` (not ``require``): the harness runs
    in two places — locally against a managed Postgres that needs TLS, AND inside
    the prod container against the internal Postgres on the Docker network, which
    does NOT support SSL. ``prefer`` negotiates TLS when the server offers it and
    falls back to plaintext when it doesn't, so the one string works in both. An
    explicit ``sslmode`` already in the URL is preserved.
    """
    for suffix in ("+asyncpg", "+psycopg", "+psycopg2"):
        url = url.replace(suffix, "")
    if "sslmode=" not in url and "localhost" not in url and "127.0.0.1" not in url:
        url += ("&" if "?" in url else "?") + "sslmode=prefer"
    return url


# ── Logging (capture to assert no secret leaks, like llm_e2e.py) ──────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("live_smoke")

# ── Deterministic canary fixture ──────────────────────────────────────────────
# uuid5 over a fixed namespace makes the ids STABLE across runs, so --seed is
# idempotent and the leaf checks always resolve the same fixture rows.
_CANARY_NS = uuid.UUID("5f3c0d2e-1a4b-4c6d-8e9f-0a1b2c3d4e5f")
CANARY_CREATOR_ID = uuid.uuid5(_CANARY_NS, "creator")
CANARY_VIDEO_ID = uuid.uuid5(_CANARY_NS, "video")
CANARY_CLIP_ID = uuid.uuid5(_CANARY_NS, "clip")
CANARY_CHANNEL = "__smoke_canary__"
CANARY_GOOGLE_SUB = "live-smoke-canary"
CANARY_R2_PREFIX = f"smoke/{CANARY_CREATOR_ID}/"

CANARY_DNA_BRIEF = (
    "This creator makes short-form cooking tutorials for beginners. Top clips: "
    "quick recipe reveals and '3-ingredient' hooks. Audience: 18-35 home cooks. "
    "Tone: warm, encouraging. Shorts work well under 45s."
)
CANARY_SEGMENTS: list[dict] = [
    {"start": 0.0, "end": 3.0, "text": "Hey everyone, welcome back to the channel."},
    {"start": 3.0, "end": 7.0, "text": "Today I'll show you the easiest pasta recipe."},
    {"start": 7.0, "end": 11.0, "text": "You only need three ingredients — salt, oil, pasta."},
    {"start": 11.0, "end": 16.0, "text": "Here's the trick most people get wrong."},
]
CANARY_TRANSCRIPT = " ".join(s["text"] for s in CANARY_SEGMENTS)
CANARY_CLIP_START_S = 0.0
CANARY_CLIP_END_S = 16.0
CANARY_PEAK_S = 11.0
CANARY_PRINCIPLE = "Backward-Look from Peak"

# Honesty disclaimer markers (mirrors llm_e2e.py).
_HONESTY_WORDS = {
    "estimate",
    "estimates",
    "grounded",
    "predicts",
    "not a guarantee",
    "cannot guarantee",
    "does not promise",
    "may",
    "likely",
    "based on",
    "suggest",
    "suggests",
    "reflects patterns",
}


# ── Result framework ───────────────────────────────────────────────────────────
@dataclass
class Results:
    passes: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    skips: list[str] = field(default_factory=list)

    def ok(self, condition: bool, name: str, detail: str = "") -> bool:
        if condition:
            logger.info("[PASS] %s", name)
            self.passes.append(name)
        else:
            msg = f"[FAIL] {name}" + (f": {detail}" if detail else "")
            logger.error(msg)
            self.failures.append(msg)
        return condition

    def skip(self, name: str, reason: str) -> None:
        logger.warning("[SKIP] %s: %s", name, reason)
        self.skips.append(f"{name}: {reason}")

    def honesty(self, text: str, name: str) -> None:
        lower = text.lower()
        self.ok(
            any(w in lower for w in _HONESTY_WORDS),
            f"{name}: honesty disclaimer present",
            f"text[:160]={text[:160]!r}",
        )


# ── DB helpers (sync psycopg — mirrors the proven clip_pipeline_state.py) ──────
def _pg_connect():
    import psycopg

    raw = os.environ.get("DATABASE_URL")
    if not raw:
        raise RuntimeError("DATABASE_URL not set for the selected --target env file")
    return psycopg.connect(_normalize_pg(raw), connect_timeout=15)


def _seed(res: Results) -> None:
    """Create/refresh the canary creator + video (ingest done, with transcript
    and signals) + one clip. Idempotent via fixed uuid5 ids and UPSERT."""
    with _pg_connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO creators (id, google_sub, channel_title, onboarding_state, "
            "minutes_balance, analysis_mode, created_at) "
            "VALUES (%s, %s, %s, 'active', 1000, 'auto', now()) "
            "ON CONFLICT (id) DO UPDATE SET channel_title = EXCLUDED.channel_title",
            (CANARY_CREATOR_ID, CANARY_GOOGLE_SUB, CANARY_CHANNEL),
        )
        # Set the canary's RLS context for the connection so the tenant-scoped
        # inserts below pass the per-creator WITH CHECK policy when the app role
        # has RLS enforced. Harmless when the role bypasses RLS (table owner).
        # `creators` is RLS-exempt, so the insert above did not need it.
        cur.execute("SELECT set_config('app.creator_id', %s, false)", (str(CANARY_CREATOR_ID),))
        cur.execute(
            "INSERT INTO videos (id, creator_id, title, kind, duration_s, source_uri, "
            "origin, ingest_status, created_at, ingest_done_at) "
            "VALUES (%s, %s, %s, 'long', %s, %s, 'upload', 'done', now(), now()) "
            "ON CONFLICT (id) DO UPDATE SET ingest_status = 'done'",
            (
                CANARY_VIDEO_ID,
                CANARY_CREATOR_ID,
                "Live-smoke canary video",
                CANARY_CLIP_END_S,
                f"s3://{os.environ.get('R2_BUCKET', 'bucket')}/{CANARY_R2_PREFIX}source.mp4",
            ),
        )
        cur.execute(
            "INSERT INTO transcripts (video_id, source, segments_jsonb) "
            "VALUES (%s, 'canary', %s::jsonb) "
            "ON CONFLICT (video_id) DO UPDATE SET segments_jsonb = EXCLUDED.segments_jsonb",
            (CANARY_VIDEO_ID, _json({"segments": CANARY_SEGMENTS})),
        )
        cur.execute(
            "INSERT INTO signals (video_id, timeline_jsonb) VALUES (%s, %s::jsonb) "
            "ON CONFLICT (video_id) DO NOTHING",
            (CANARY_VIDEO_ID, _json({"events": []})),
        )
        cur.execute(
            "INSERT INTO clips (id, video_id, creator_id, setup_start_s, start_s, end_s, "
            "peak_s, score, signals_jsonb, format, render_status, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, 'short', 'pending', now()) "
            "ON CONFLICT (id) DO UPDATE SET render_status = 'pending'",
            (
                CANARY_CLIP_ID,
                CANARY_VIDEO_ID,
                CANARY_CREATOR_ID,
                CANARY_CLIP_START_S,
                CANARY_CLIP_START_S,
                CANARY_CLIP_END_S,
                CANARY_PEAK_S,
                0.82,
                _json({"principle": CANARY_PRINCIPLE}),
            ),
        )
        conn.commit()
    res.ok(True, "seed: canary creator/video/transcript/signals/clip upserted")


def _teardown(res: Results) -> None:
    """Purge the canary fixture (FK cascade removes child rows) + its R2 prefix."""
    with _pg_connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM creators WHERE id = %s", (CANARY_CREATOR_ID,))
        conn.commit()
    res.ok(True, "teardown: canary creator deleted (cascade)")
    try:
        from worker import storage

        n = storage.delete_prefix(CANARY_R2_PREFIX)
        res.ok(True, f"teardown: purged {n} R2 object(s) under {CANARY_R2_PREFIX}")
    except Exception as exc:  # noqa: BLE001 — best-effort cleanup
        res.skip("teardown_r2", f"{type(exc).__name__}: {exc}")


def _json(obj: object) -> str:
    import json

    return json.dumps(obj)


# ── Checks ─────────────────────────────────────────────────────────────────────
def check_db(res: Results) -> None:
    """Target Postgres reachable and the canary fixture is present."""
    with _pg_connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")
        res.ok(cur.fetchone() == (1,), "db: SELECT 1 round-trips")
        cur.execute("SELECT 1 FROM creators WHERE id = %s", (CANARY_CREATOR_ID,))
        res.ok(cur.fetchone() is not None, "db: canary creator exists (run --seed if not)")


def check_isolation(res: Results) -> None:
    """Per-creator RLS isolation is live: the canary's clip is visible under the
    canary's RLS context and INVISIBLE under a different creator's context."""
    with _pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.creator_id', %s, true)", (str(CANARY_CREATOR_ID),))
            cur.execute("SELECT count(*) FROM clips WHERE id = %s", (CANARY_CLIP_ID,))
            row = cur.fetchone()
            res.ok(row is not None and row[0] == 1, "isolation: canary sees its own clip under RLS")
        conn.rollback()  # drop the SET LOCAL
        with conn.cursor() as cur:
            other = uuid.uuid5(_CANARY_NS, "other-creator")
            cur.execute("SELECT set_config('app.creator_id', %s, true)", (str(other),))
            cur.execute("SELECT count(*) FROM clips WHERE id = %s", (CANARY_CLIP_ID,))
            row = cur.fetchone()
            res.ok(
                row is not None and row[0] == 0,
                "isolation: a different creator sees ZERO canary clips (no cross-tenant leak)",
            )
        conn.rollback()


def check_pipeline(res: Results) -> None:
    """Tier-1 checkpointed read of the indivisible upstream chain. Read-only:
    asserts the canary reached each stage. A full live re-run (real
    ingest+Deepgram+ffmpeg) is intentionally NOT auto-triggered here — it is
    metered/destructive; trigger it deliberately on staging."""
    with _pg_connect() as conn, conn.cursor() as cur:
        # videos/clips are RLS-protected; set the canary's tenant context so these
        # reads return its rows once the app role enforces RLS (transcripts/signals
        # are child tables without a policy, but the GUC is harmless for them).
        cur.execute("SELECT set_config('app.creator_id', %s, false)", (str(CANARY_CREATOR_ID),))
        cur.execute("SELECT ingest_status FROM videos WHERE id = %s", (CANARY_VIDEO_ID,))
        row = cur.fetchone()
        ingest = row[0] if row else None
        res.ok(
            str(ingest) in ("done", "IngestStatus.done"),
            "pipeline: ingest_status=done",
            str(ingest),
        )
        cur.execute("SELECT 1 FROM transcripts WHERE video_id = %s", (CANARY_VIDEO_ID,))
        res.ok(cur.fetchone() is not None, "pipeline: transcript row present")
        cur.execute("SELECT 1 FROM signals WHERE video_id = %s", (CANARY_VIDEO_ID,))
        res.ok(cur.fetchone() is not None, "pipeline: signals row present")
        cur.execute("SELECT count(*) FROM clips WHERE video_id = %s", (CANARY_VIDEO_ID,))
        row = cur.fetchone()
        res.ok(row is not None and row[0] >= 1, "pipeline: >=1 clip candidate generated")


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _make_source_clip(dst: Path, seconds: int = 18) -> None:
    """Generate a tiny synthetic source video (testsrc + sine) via ffmpeg lavfi."""
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size=1280x720:rate=24:duration={seconds}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            "-shortest",
            str(dst),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )


def _probe_dims(path: Path) -> tuple[int, int]:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=s=x:p=0",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()
    w, _, h = out.partition("x")
    return int(w), int(h)


def check_render(res: Results) -> None:
    """Real ffmpeg render of the canary clip to a 9:16 short."""
    if not _ffmpeg_available():
        res.skip("render", "ffmpeg/ffprobe not on PATH (runs on the VM / render-env)")
        return
    from clip_engine.render import render_clip_file

    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "source.mp4"
        out = Path(td) / "clip.mp4"
        _make_source_clip(src)
        render_clip_file(
            source_path=src,
            start_s=CANARY_CLIP_START_S,
            end_s=CANARY_CLIP_END_S,
            out_path=out,
            peak_s=CANARY_PEAK_S,
        )
        res.ok(out.exists() and out.stat().st_size > 0, "render: output mp4 written, non-empty")
        w, h = _probe_dims(out)
        res.ok(h > w, "render: output is portrait 9:16 (h>w)", f"{w}x{h}")


def check_clean(res: Results) -> None:
    """Real ffmpeg cleaned-render (filler/silence cut) of the canary clip."""
    if not _ffmpeg_available():
        res.skip("clean", "ffmpeg/ffprobe not on PATH (runs on the VM / render-env)")
        return
    from clip_engine.render import render_cleaned_clip_file

    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "source.mp4"
        out = Path(td) / "clean.mp4"
        _make_source_clip(src)
        # Keep two sub-ranges (drop the middle) — a minimal valid cut list.
        render_cleaned_clip_file(
            source_path=src,
            keep_ranges=[(0.0, 6.0), (10.0, 16.0)],
            out_path=out,
        )
        res.ok(out.exists() and out.stat().st_size > 0, "clean: cleaned mp4 written, non-empty")


def check_title(res: Results, with_llm: bool) -> None:
    if not with_llm:
        res.skip("title", "metered LLM check — pass --with-llm to run")
        return
    from knowledge.clip_titles import generate_clip_title_suggestions

    result, usage = generate_clip_title_suggestions(
        channel_title=CANARY_CHANNEL, dna_brief=CANARY_DNA_BRIEF, clip_transcript=CANARY_TRANSCRIPT
    )
    res.ok(bool(result.get("titles")), "title: titles non-empty")
    res.honesty(str(result.get("disclaimer", "")), "title")
    res.ok(usage.get("input_tokens", 0) > 0, "title: usage recorded", str(usage))


def check_caption(res: Results, with_llm: bool) -> None:
    if not with_llm:
        res.skip("caption", "metered LLM check — pass --with-llm to run")
        return
    from knowledge.clip_captions import generate_clip_caption_hooks

    result, usage = generate_clip_caption_hooks(
        channel_title=CANARY_CHANNEL, dna_brief=CANARY_DNA_BRIEF, clip_hook=CANARY_TRANSCRIPT[:200]
    )
    res.ok(bool(result.get("options")), "caption: options non-empty")
    res.honesty(str(result.get("disclaimer", "")), "caption")
    res.ok(usage.get("input_tokens", 0) > 0, "caption: usage recorded", str(usage))


def check_explain(res: Results, with_llm: bool) -> None:
    if not with_llm:
        res.skip("explain", "metered LLM check — pass --with-llm to run")
        return
    from knowledge.clip_explain import generate_clip_explanation

    result, usage = generate_clip_explanation(
        channel_title=CANARY_CHANNEL,
        dna_brief=CANARY_DNA_BRIEF,
        clip_principle=CANARY_PRINCIPLE,
        clip_score=0.82,
        clip_start_s=CANARY_CLIP_START_S,
        clip_end_s=CANARY_CLIP_END_S,
        clip_transcript=CANARY_TRANSCRIPT,
    )
    res.ok(bool(result.get("cited_principle")), "explain: cites a named principle")
    res.ok(usage.get("input_tokens", 0) > 0, "explain: usage recorded", str(usage))


def check_publish(res: Results, target: str, publish_live: bool) -> None:
    """Destructive synthetic transaction → dry-run pre-flight by default.

    Asserts the publish path is reachable up to the upload boundary WITHOUT
    calling videos.insert. A real upload is only honored on staging with an
    explicit --publish-live, against the throwaway canary channel."""
    if publish_live and target == "staging":
        res.skip(
            "publish",
            "real upload path not wired in the harness — requires a dedicated "
            "staging test channel + OAuth (out of scope for v1; see Issue 341)",
        )
        return
    if publish_live and target != "staging":
        res.ok(False, "publish: --publish-live refused on non-staging target (safety)", target)
        return
    # Dry-run: prove the module imports and the pre-flight surface exists.
    try:
        from youtube.publish import YouTubeUploadError  # noqa: F401

        res.ok(True, "publish: pre-flight surface importable (dry-run; no real upload)")
    except Exception as exc:  # noqa: BLE001
        res.ok(False, "publish: pre-flight import failed", f"{type(exc).__name__}: {exc}")


def check_r2(res: Results) -> None:
    """Target R2 reachable; the canary prefix is listable (read-only)."""
    try:
        from worker import storage

        client = storage._r2()  # noqa: SLF001 — diagnostic reuse of the singleton
        bucket = os.environ["R2_BUCKET"]
        client.list_objects_v2(Bucket=bucket, Prefix=CANARY_R2_PREFIX, MaxKeys=1)
        res.ok(True, "r2: bucket reachable + canary prefix listable")
    except KeyError:
        res.skip("r2", "R2_BUCKET not set for the selected target")
    except Exception as exc:  # noqa: BLE001
        res.ok(False, "r2: list_objects failed", f"{type(exc).__name__}: {exc}")


# ── Registry + main ────────────────────────────────────────────────────────────
# name → callable(res, ctx). ctx carries the parsed flags the check needs.
def _registry(args: argparse.Namespace):
    return {
        "db": lambda res: check_db(res),
        "isolation": lambda res: check_isolation(res),
        "pipeline": lambda res: check_pipeline(res),
        "render": lambda res: check_render(res),
        "clean": lambda res: check_clean(res),
        "title": lambda res: check_title(res, args.with_llm),
        "caption": lambda res: check_caption(res, args.with_llm),
        "explain": lambda res: check_explain(res, args.with_llm),
        "publish": lambda res: check_publish(res, args.target, args.publish_live),
        "r2": lambda res: check_r2(res),
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Live-in-isolation smoke-test harness (Issue 341)")
    p.add_argument(
        "--target",
        choices=("prod", "staging"),
        default="prod",
        help="which env file to load: prod=.env, staging=.env.staging",
    )
    p.add_argument(
        "--only",
        choices=tuple(_ALL_CHECKS),
        default=None,
        help="run a single capability check in isolation",
    )
    p.add_argument(
        "--seed", action="store_true", help="create/refresh the canary fixture, then run"
    )
    p.add_argument("--teardown", action="store_true", help="purge the canary fixture and exit")
    p.add_argument("--with-llm", action="store_true", help="include the metered LLM leaf checks")
    p.add_argument(
        "--publish-live",
        action="store_true",
        help="attempt a REAL upload (staging only; refused on prod)",
    )
    return p.parse_args(argv)


_ALL_CHECKS = (
    "db",
    "isolation",
    "pipeline",
    "render",
    "clean",
    "title",
    "caption",
    "explain",
    "publish",
    "r2",
)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)

    if os.environ.get("RUN_LIVE_SMOKE") != "1":
        print(
            "live_smoke.py: RUN_LIVE_SMOKE is not '1' — skipping live checks.\n"
            "Set RUN_LIVE_SMOKE=1 and select a --target with a populated env file to run."
        )
        return 0

    env_file = ".env" if args.target == "prod" else ".env.staging"
    loaded = _load_env(env_file)
    if loaded:
        logger.info("live_smoke: target=%s env=%s", args.target, env_file)
    elif os.environ.get("DATABASE_URL"):
        # In a container the env is injected directly (no .env file on disk).
        # Fall back to the ambient environment rather than hard-failing.
        logger.info(
            "live_smoke: target=%s env=ambient (no %s on disk; using injected environment)",
            args.target,
            env_file,
        )
    else:
        print(
            f"live_smoke.py: env file {env_file!r} not found for --target {args.target} "
            "and DATABASE_URL is not in the environment.",
            file=sys.stderr,
        )
        return 1

    res = Results()

    if args.teardown:
        _teardown(res)
        return _report(res)

    if args.seed:
        _seed(res)

    registry = _registry(args)
    names = [args.only] if args.only else list(_ALL_CHECKS)
    for name in names:
        logger.info("--- check: %s ---", name)
        try:
            registry[name](res)
        except Exception as exc:  # noqa: BLE001 — one check must not abort the run
            logger.exception("check %s crashed", name)
            res.failures.append(f"[FAIL] {name}: unexpected {type(exc).__name__}: {exc}")

    return _report(res)


def _report(res: Results) -> int:
    print(
        f"\nResults: {len(res.passes)} passed, {len(res.failures)} failed, {len(res.skips)} skipped"
    )
    for s in res.skips:
        print(f"[SKIP] {s}")
    for f in res.failures:
        print(f)
    return 0 if not res.failures else 1


if __name__ == "__main__":
    sys.exit(main())
