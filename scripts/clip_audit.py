"""Read-only post-render clip audit — "are the rendered clips actually good?".

Two subcommands, run in two different places because the credentials and the
eyes live in different machines:

``manifest``
    Runs **in the prod app container**. Pure SELECT + presign; never writes.
    Emits one JSON document describing a video's clips: row fields, parsed
    ``reframe_track_jsonb`` geometry stats, style preset, transcript slices
    (window plus lead-in / lead-out, for judging whether a clip carries its own
    context), presigned media URLs, and the feedback / preference / minute
    sections needed to verify a Review session landed.

    The app role is ``creatorclip_app`` with FORCE ROW LEVEL SECURITY on every
    tenant table, so every query is preceded by ``set_config('app.creator_id')``.
    ``creators`` is RLS-exempt, which is what makes auto-discovery possible.

``inspect``
    Runs **locally**. Needs only ffmpeg and network — no DB, no R2 credentials.
    Downloads each presigned clip, probes it, measures loudness, and renders a
    contact sheet per clip plus a before/after sheet per framing cut.

Usage::

    # prod side — piped over stdin so no image rebuild is needed to run it
    ssh creatorclip-vm 'cd /opt/autoclip && docker compose -f docker-compose.prod.yml \
        exec -T app python3.12 - --video <uuid>' < scripts/clip_audit.py > manifest.json

    # local side
    python3.12 scripts/clip_audit.py inspect --manifest manifest.json --out ./audit
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

# ── Track geometry ──────────────────────────────────────────────────────────

# A "direction flip" is only meaningful for moves that are actually visible.
# Sub-pixel wobble in a densified glide ramp is not jitter, it is the ramp.
_JITTER_MIN_PX = 8.0


def track_stats(track: dict[str, Any] | None) -> dict[str, Any]:
    """Summarize a ``reframe_track_jsonb`` document into audit-ready numbers.

    Pure function over the v1 track schema (``clip_engine/reframe.py``):
    ``{mode, source, crop, origin_s, duration_s, keyframes:[{t,x}], cuts:[...],
    shots:[...], speakers:{...}, meta:{...}, region?}``. ``region`` is emitted
    only when a camera region was detected, so its absence means no chrome
    removal happened on that render.
    """
    if not track:
        return {"present": False}

    keyframes = track.get("keyframes") or []
    xs = [float(k["x"]) for k in keyframes if "x" in k]
    # Sliding pairs — the offset slice is shorter by one on purpose.
    deltas = [round(b - a, 2) for a, b in zip(xs, xs[1:], strict=False)]
    moving = [d for d in deltas if abs(d) >= _JITTER_MIN_PX]
    flips = sum(1 for a, b in zip(moving, moving[1:], strict=False) if a * b < 0)
    duration_s = float(track.get("duration_s") or 0.0)

    region = track.get("region")
    speakers = track.get("speakers") or {}
    meta = track.get("meta") or {}
    return {
        "present": True,
        "mode": track.get("mode"),
        "keyframes": len(keyframes),
        "cuts": len(track.get("cuts") or []),
        "cut_times": [round(float(c["t"]), 3) for c in (track.get("cuts") or []) if "t" in c],
        "shots": len(track.get("shots") or []),
        "x_range": [min(xs), max(xs)] if xs else None,
        "max_move_px": max((abs(d) for d in deltas), default=0.0),
        "visible_moves": len(moving),
        "moves_per_s": round(len(moving) / duration_s, 3) if duration_s else None,
        "direction_flips": flips,
        "region": region,
        "chrome_removed": region is not None,
        "mapping_confidence": speakers.get("mapping_confidence"),
        "speaker_count": speakers.get("count"),
        "fallback": meta.get("fallback"),
        "source": track.get("source"),
        "crop": track.get("crop"),
        "duration_s": duration_s,
    }


def _segment_text(segments: list[dict[str, Any]], start_s: float, end_s: float) -> str:
    """Join transcript segment text overlapping ``[start_s, end_s]``."""
    parts = [
        str(seg.get("text", "")).strip()
        for seg in segments
        if float(seg.get("end", 0.0)) > start_s and float(seg.get("start", 0.0)) < end_s
    ]
    return " ".join(p for p in parts if p)


# ── manifest (prod side) ────────────────────────────────────────────────────

LEAD_IN_S = 30.0
LEAD_OUT_S = 15.0


def _normalize(url: str) -> str:
    """psycopg wants a plain postgresql:// URL with no SQLAlchemy driver suffix."""
    for suffix in ("+asyncpg", "+psycopg", "+psycopg2"):
        url = url.replace(suffix, "")
    local_hosts = ("localhost", "127.0.0.1", "postgres", "db")
    if "sslmode=" not in url and not any(h in url for h in local_hosts):
        url += ("&" if "?" in url else "?") + "sslmode=require"
    return url


def _rows(cur: Any, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    cur.execute(sql, params)
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def _presign(uri: str | None, filename: str) -> str | None:
    """Presign a stored object, tolerating local-disk dev and missing creds."""
    if not uri:
        return None
    try:
        from worker.storage import presigned_download_url

        return presigned_download_url(uri, filename=filename, disposition="inline", expires_s=3600)
    except Exception as exc:  # diagnostic script: a presign failure is data, not a crash
        return f"<presign failed: {type(exc).__name__}: {exc}>"


def _discover(cur: Any) -> tuple[str, str]:
    """Newest video that has at least one rendered clip, across all creators."""
    best: tuple[Any, str, str] | None = None
    for creator in _rows(cur, "SELECT id, channel_title FROM creators"):
        cur.execute("SELECT set_config('app.creator_id', %s, false)", (str(creator["id"]),))
        found = _rows(
            cur,
            """SELECT v.id, v.created_at FROM videos v
               JOIN clips c ON c.video_id = v.id AND c.render_uri IS NOT NULL
               GROUP BY v.id, v.created_at ORDER BY v.created_at DESC LIMIT 1""",
        )
        if found and (best is None or found[0]["created_at"] > best[0]):
            best = (found[0]["created_at"], str(creator["id"]), str(found[0]["id"]))
    if best is None:
        raise SystemExit("No video with a rendered clip found.")
    return best[1], best[2]


def cmd_manifest(args: argparse.Namespace) -> None:
    import psycopg

    raw = os.environ.get("DATABASE_URL")
    if not raw:
        raise SystemExit("DATABASE_URL not set — run this inside the app container.")

    with psycopg.connect(_normalize(raw), connect_timeout=20) as conn, conn.cursor() as cur:
        creator_id, video_id = args.creator, args.video
        if not creator_id or not video_id:
            discovered_creator, discovered_video = _discover(cur)
            creator_id = creator_id or discovered_creator
            video_id = video_id or discovered_video
        cur.execute("SELECT set_config('app.creator_id', %s, false)", (creator_id,))

        video = _rows(
            cur,
            """SELECT id, title, duration_s, source_uri, poster_uri, ingest_status,
                      origin, created_at FROM videos WHERE id = %s""",
            (video_id,),
        )
        if not video:
            raise SystemExit(f"Video {video_id} not visible for creator {creator_id}.")
        vid = video[0]

        clips = _rows(
            cur,
            """SELECT id, rank, setup_start_s, start_s, end_s, peak_s, score, dna_match,
                      render_status, render_uri, cleaned_render_uri, poster_uri,
                      applied_title, suggested_title, suggested_hook, style_preset,
                      signals_jsonb, reframe_track_jsonb, created_at
               FROM clips WHERE video_id = %s ORDER BY rank NULLS LAST, created_at""",
            (video_id,),
        )

        transcript = _rows(
            cur, "SELECT source, segments_jsonb FROM transcripts WHERE video_id = %s", (video_id,)
        )
        segments: list[dict[str, Any]] = []
        if transcript:
            segments = (transcript[0]["segments_jsonb"] or {}).get("segments") or []

        out_clips = []
        for c in clips:
            render_start = c["setup_start_s"] if c["setup_start_s"] is not None else c["start_s"]
            render_start = float(render_start or 0.0)
            end_s = float(c["end_s"] or 0.0)
            signals = c["signals_jsonb"] or {}
            out_clips.append(
                {
                    "id": str(c["id"]),
                    "rank": c["rank"],
                    "setup_start_s": c["setup_start_s"],
                    "start_s": c["start_s"],
                    "end_s": c["end_s"],
                    "peak_s": c["peak_s"],
                    "render_start_s": render_start,
                    "duration_s": round(end_s - render_start, 3),
                    "score": c["score"],
                    "dna_match": c["dna_match"],
                    "render_status": c["render_status"],
                    "render_uri": c["render_uri"],
                    "cleaned_render_uri": c["cleaned_render_uri"],
                    "poster_uri": c["poster_uri"],
                    "applied_title": c["applied_title"],
                    "suggested_title": c["suggested_title"],
                    "suggested_hook": c["suggested_hook"],
                    "style_preset": c["style_preset"],
                    "principle": signals.get("principle"),
                    "reasoning": signals.get("reasoning"),
                    "origin": signals.get("origin"),
                    "track": track_stats(c["reframe_track_jsonb"]),
                    "transcript": {
                        "lead_in": _segment_text(segments, render_start - LEAD_IN_S, render_start),
                        "window": _segment_text(segments, render_start, end_s),
                        "lead_out": _segment_text(segments, end_s, end_s + LEAD_OUT_S),
                    },
                    "media_url": _presign(c["render_uri"], f"clip-{c['id']}.mp4"),
                    "poster_url": _presign(c["poster_uri"], f"poster-{c['id']}.jpg"),
                }
            )

        manifest = {
            "creator_id": creator_id,
            "video": {
                "id": str(vid["id"]),
                "title": vid["title"],
                "duration_s": vid["duration_s"],
                "ingest_status": str(vid["ingest_status"]),
                "origin": str(vid["origin"]),
                "created_at": str(vid["created_at"]),
                "source_present": bool(vid["source_uri"]),
                "source_url": _presign(vid["source_uri"], "source.mp4"),
            },
            "transcript_source": transcript[0]["source"] if transcript else None,
            "transcript_segments": len(segments),
            "clips": out_clips,
            "feedback": _rows(
                cur,
                """SELECT id, clip_id, action, feedback_tags, feedback_note, created_at
                   FROM clip_feedback ORDER BY created_at DESC LIMIT 25""",
            ),
            "preference_models": _rows(
                cur,
                """SELECT id, version, metrics_jsonb, updated_at FROM preference_models
                   ORDER BY updated_at DESC LIMIT 5""",
            ),
            "minute_deductions": _rows(
                cur,
                """SELECT id, video_id, minutes_deducted, duration_s, deducted_at
                   FROM minute_deductions ORDER BY deducted_at DESC LIMIT 25""",
            ),
        }

    print(json.dumps(manifest, indent=2, default=str))


# ── inspect (local side) ────────────────────────────────────────────────────


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def _probe(path: Path) -> dict[str, Any]:
    proc = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(path),
        ]
    )
    if proc.returncode != 0:
        return {"error": proc.stderr.strip()[:400]}
    data = json.loads(proc.stdout)
    video = next((s for s in data["streams"] if s["codec_type"] == "video"), {})
    audio = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    return {
        "width": video.get("width"),
        "height": video.get("height"),
        "video_codec": video.get("codec_name"),
        "fps": video.get("avg_frame_rate"),
        "duration_s": round(float(data["format"].get("duration", 0.0)), 3),
        "size_bytes": int(data["format"].get("size", 0)),
        "audio_codec": audio.get("codec_name") if audio else None,
        "audio_channels": audio.get("channels") if audio else None,
        "has_audio": audio is not None,
    }


def _loudness(path: Path) -> dict[str, Any]:
    """Integrated loudness of the delivered file (render targets I=-14 LUFS)."""
    proc = _run(
        [
            "ffmpeg",
            "-nostdin",
            "-i",
            str(path),
            "-af",
            "ebur128=framelog=verbose",
            "-f",
            "null",
            "-",
        ]
    )
    summary = proc.stderr.rsplit("Summary:", 1)
    if len(summary) != 2:
        return {"error": "no ebur128 summary"}
    out: dict[str, Any] = {}
    for line in summary[1].splitlines():
        if "I:" in line and "LUFS" in line:
            out["integrated_lufs"] = float(line.split("I:")[1].split("LUFS")[0].strip())
        elif "LRA:" in line and "LU" in line and "low" not in line and "high" not in line:
            out["lra"] = float(line.split("LRA:")[1].split("LU")[0].strip())
        elif "Peak:" in line and "dBFS" in line:
            out["true_peak_dbfs"] = float(line.split("Peak:")[1].split("dBFS")[0].strip())
    return out


def _frame(src: Path, t: float, dest: Path, width: int = 270) -> bool:
    proc = _run(
        [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-ss",
            f"{max(t, 0.0):.3f}",
            "-i",
            str(src),
            "-frames:v",
            "1",
            "-vf",
            f"scale={width}:-2",
            "-q:v",
            "3",
            str(dest),
        ]
    )
    return proc.returncode == 0 and dest.exists()


def _tile(frames: list[Path], cols: int, dest: Path) -> bool:
    if not frames:
        return False
    listing = dest.with_suffix(".txt")
    listing.write_text("".join(f"file '{f}'\n" for f in frames))
    rows = (len(frames) + cols - 1) // cols
    proc = _run(
        [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(listing),
            "-vf",
            f"tile={cols}x{rows}:padding=4:margin=4:color=0x202020",
            "-frames:v",
            "1",
            "-q:v",
            "3",
            str(dest),
        ]
    )
    listing.unlink(missing_ok=True)
    return proc.returncode == 0 and dest.exists()


def _download(url: str, dest: Path) -> None:
    with urllib.request.urlopen(url, timeout=180) as resp, dest.open("wb") as fh:
        while chunk := resp.read(1 << 20):
            fh.write(chunk)


def cmd_inspect(args: argparse.Namespace) -> None:
    manifest = json.loads(Path(args.manifest).read_text())
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    media_dir = out_dir / "media"
    media_dir.mkdir(exist_ok=True)

    results = []
    for clip in manifest["clips"]:
        if not clip.get("media_url") or str(clip["media_url"]).startswith("<presign failed"):
            results.append({"id": clip["id"], "rank": clip["rank"], "skipped": "no media url"})
            continue
        cid, rank = clip["id"], clip["rank"]
        tag = f"rank{rank:02d}" if isinstance(rank, int) else cid[:8]
        mp4 = media_dir / f"{tag}-{cid[:8]}.mp4"
        if not mp4.exists():
            _download(clip["media_url"], mp4)

        probe = _probe(mp4)
        entry: dict[str, Any] = {
            "id": cid,
            "rank": rank,
            "file": str(mp4),
            "probe": probe,
            "loudness": _loudness(mp4),
        }

        duration = probe.get("duration_s") or clip.get("duration_s") or 0.0
        if duration:
            times = [duration * (i + 0.5) / args.frames for i in range(args.frames)]
            frames = []
            for i, t in enumerate(times):
                fp = media_dir / f"{tag}-f{i:02d}.jpg"
                if _frame(mp4, t, fp):
                    frames.append(fp)
            sheet = out_dir / f"{tag}-contact.jpg"
            entry["contact_sheet"] = str(sheet) if _tile(frames, 4, sheet) else None
            entry["contact_times"] = [round(t, 2) for t in times]

        cut_times = clip.get("track", {}).get("cut_times") or []
        if cut_times:
            pairs = []
            for i, t in enumerate(cut_times[: args.max_cuts]):
                for j, offset in enumerate((-0.2, 0.2)):
                    fp = media_dir / f"{tag}-cut{i:02d}{j}.jpg"
                    if _frame(mp4, t + offset, fp):
                        pairs.append(fp)
            sheet = out_dir / f"{tag}-cuts.jpg"
            entry["cut_sheet"] = str(sheet) if _tile(pairs, 2, sheet) else None
        results.append(entry)

    report = out_dir / "inspect.json"
    report.write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command")

    man = sub.add_parser("manifest", help="prod-side read-only manifest (default)")
    man.add_argument("--creator")
    man.add_argument("--video")

    ins = sub.add_parser("inspect", help="local-side download + probe + frame sheets")
    ins.add_argument("--manifest", required=True)
    ins.add_argument("--out", required=True)
    ins.add_argument("--frames", type=int, default=8)
    ins.add_argument("--max-cuts", type=int, default=6)

    # Piped over stdin the argv is ``['-', '--video', ...]``, so default to manifest.
    argv = sys.argv[1:]
    if not argv or argv[0].startswith("-"):
        argv = ["manifest", *argv]
    args = parser.parse_args(argv)

    if args.command == "inspect":
        cmd_inspect(args)
    else:
        cmd_manifest(args)


if __name__ == "__main__":
    main()
