"""Celery queue routing pins (Issues 432, 507).

ffmpeg-render tasks must route to the dedicated `render` queue (consumed with
concurrency 1) — parallel renders starve each other into the render timeout on
a small box. Everything else stays on the default queue. These pins keep a new
render task or a rename from silently landing back on the parallel worker.

Issue 507 replaced the scope literal. ``test_every_ffmpeg_task_is_routed`` used
to read ``expected = {five literals}; assert set(RENDER_TASKS) == expected`` —
a literal compared to a copy of itself, which cannot fail for any reason anyone
cares about. The population is now DISCOVERED from the call graph, and every
ffmpeg-reaching task must be either routed or carry a written exemption.
"""

from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path

from worker.celery_app import RENDER_QUEUE, RENDER_TASKS, celery

_REPO_ROOT = Path(__file__).resolve().parents[1]

# Package roots that count as first-party for import resolution. A call leaving
# this set (subprocess, boto3, anthropic) cannot reach our ffmpeg helpers.
_FIRST_PARTY = {
    "worker",
    "clip_engine",
    "ingestion",
    "dna",
    "knowledge",
    "preference",
    "upload_intel",
    "improvement",
    "youtube",
    "routers",
    "billing",
    "chat",
    "notify",
    "analysis",
}

_SWEEP_EXCLUDE_DIRS = {
    ".venv",
    ".git",
    ".claude",
    "node_modules",
    "tests",
    "alembic",
    "scripts",
    "frontend",
    "static",
    "docs",
    "deploy",
}

# Celery's enqueue verbs. A reference to ``other_task.delay`` hands work to the
# BROKER — the callee runs in whichever worker consumes its queue, so it is not
# an in-process edge. Following it was the defect in this test's first draft:
# `generate_clips` and `build_signals` appeared to reach ffmpeg, when in fact
# both only call `render_video_clips.delay(...)` and the encode happens inside
# that already-routed task, on the render worker. Exactly right, not a leak.
_ENQUEUE_VERBS = {"delay", "apply_async", "s", "si", "signature", "chunks", "starmap"}

# The argv token that means "this function spawns an ffmpeg process". `ffprobe`
# is deliberately NOT included: it is a metadata read, not an encode, and the
# render queue exists for CPU-saturating encodes (see RENDER_QUEUE's comment).
_FFMPEG_TOKEN = "ffmpeg"


# Every ffmpeg-reaching Celery task that deliberately does NOT route to the
# render queue, with the reason. An entry here is a decision, not a suppression:
# the queue exists because a libx264 encode saturates the box, and none of these
# encodes video. Bidirectionally checked below — a stale entry fails too.
_ROUTING_EXEMPTIONS: dict[str, str] = {
    "worker.tasks.ingest_video": (
        "Extracts the audio track (`extract_audio_wav`) — a decode to WAV, no video "
        "encode. Routing it to the concurrency-1 render queue would serialize the "
        "ENTIRE ingestion pipeline behind whatever clip is rendering, which is a far "
        "worse failure than the CPU contention the queue exists to prevent."
    ),
    "worker.tasks.backfill_video_posters": (
        "`_poster_cmd` seeks to one timestamp and writes a single JPEG. Sub-second, "
        "no encode; queueing it behind a multi-minute render would make posters "
        "arrive later for no benefit."
    ),
    "worker.tasks.backfill_video_camera_regions": (
        "Decodes sampled grayscale frames for overlay-span detection. Heavier than "
        "the other two and the closest call here — but it is a decode-and-analyze "
        "pass, not an encode, and it is a batched backfill that already runs off the "
        "critical path. Revisit if render timeouts are ever traced to it."
    ),
}


def _iter_first_party_modules() -> list[tuple[str, Path]]:
    """Every first-party module, as (dotted_name, path)."""
    out: list[tuple[str, Path]] = []
    for path in _REPO_ROOT.rglob("*.py"):
        rel = path.relative_to(_REPO_ROOT)
        if any(part in _SWEEP_EXCLUDE_DIRS for part in rel.parts):
            continue
        if rel.parts[0] not in _FIRST_PARTY and len(rel.parts) > 1:
            continue
        mod = str(rel.with_suffix("")).replace("/", ".")
        if mod.endswith(".__init__"):
            mod = mod[: -len(".__init__")]
        out.append((mod, path))
    return out


def _spawns_ffmpeg(node: ast.AST) -> bool:
    return any(
        isinstance(sub, ast.Constant) and sub.value == _FFMPEG_TOKEN for sub in ast.walk(node)
    )


def _enqueued_name_ids(node: ast.AST) -> set[int]:
    """id()s of Name nodes referenced only as `<name>.delay`-style enqueues."""
    ids: set[int] = set()
    for sub in ast.walk(node):
        if (
            isinstance(sub, ast.Attribute)
            and sub.attr in _ENQUEUE_VERBS
            and isinstance(sub.value, ast.Name)
        ):
            ids.add(id(sub.value))
    return ids


def _build_call_graph() -> tuple[dict[tuple[str, str], set[tuple[str, str]]], set[tuple[str, str]]]:
    """Return (edges, direct_ffmpeg_spawners) over first-party top-level functions."""
    funcs: dict[tuple[str, str], ast.AST] = {}
    imports: dict[str, dict[str, tuple[str, str]]] = {}
    direct: set[tuple[str, str]] = set()

    for mod, path in _iter_first_party_modules():
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # pragma: no cover - a syntax error fails elsewhere, loudly
            continue
        imports[mod] = {}
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.split(".")[0] in _FIRST_PARTY
            ):
                for alias in node.names:
                    imports[mod][alias.asname or alias.name] = (node.module, alias.name)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                funcs[(mod, node.name)] = node
                if _spawns_ffmpeg(node):
                    direct.add((mod, node.name))

    edges: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for (mod, name), node in funcs.items():
        skip = _enqueued_name_ids(node)
        out: set[tuple[str, str]] = set()
        for sub in ast.walk(node):
            # References, not just Call.func: worker/tasks.py offloads every
            # blocking encode with `asyncio.to_thread(render_clip_file, ...)`,
            # where the callee is an ARGUMENT and never appears as Call.func.
            # A call-graph that only follows Call.func sees zero render edges.
            if isinstance(sub, ast.Name):
                if id(sub) in skip:
                    continue
                if (mod, sub.id) in funcs:
                    out.add((mod, sub.id))
                elif sub.id in imports.get(mod, {}):
                    target = imports[mod][sub.id]
                    if target in funcs:
                        out.add(target)
            elif isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name):
                base = sub.value.id
                if base in imports.get(mod, {}):
                    tmod, tname = imports[mod][base]
                    for cand in ((f"{tmod}.{tname}", sub.attr), (tmod, sub.attr)):
                        if cand in funcs:
                            out.add(cand)
        out.discard((mod, name))
        edges[(mod, name)] = out

    return edges, direct


def _celery_task_names() -> set[str]:
    """Fully-qualified names of every Celery task defined in worker/tasks.py."""
    tree = ast.parse((_REPO_ROOT / "worker" / "tasks.py").read_text())
    names: set[str] = set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            if ast.unparse(target).split(".")[-1] == "task":
                names.add(f"worker.tasks.{node.name}")
                break
    return names


@lru_cache(maxsize=1)
def discover_ffmpeg_reaching_tasks() -> frozenset[str]:
    """Celery tasks that reach an ffmpeg spawn IN-PROCESS, via the call graph.

    Cached: the sweep parses ~130 modules and every assertion below needs the
    same answer, so an uncached call would re-parse the repo five times.
    """
    edges, direct = _build_call_graph()

    reaching = set(direct)
    changed = True
    while changed:  # transitive closure; the graph is small (~900 nodes)
        changed = False
        for fn, outs in edges.items():
            if fn not in reaching and outs & reaching:
                reaching.add(fn)
                changed = True

    tasks = _celery_task_names()
    return frozenset(t for t in tasks if ("worker.tasks", t.rsplit(".", 1)[1]) in reaching)


def test_render_tasks_route_to_render_queue() -> None:
    routes = celery.conf.task_routes
    for name in RENDER_TASKS:
        assert routes.get(name) == {"queue": RENDER_QUEUE}, (
            f"{name} must route to the {RENDER_QUEUE!r} queue — parallel ffmpeg "
            "encodes starve each other into the render timeout (Issue 432)"
        )


def test_render_task_names_are_registered() -> None:
    """A typo in RENDER_TASKS would route nothing while looking configured."""
    import worker.tasks  # noqa: F401 — registers the task names

    for name in RENDER_TASKS:
        assert name in celery.tasks, f"{name} is not a registered Celery task"


# ── Derived scope: every ffmpeg task is routed or exempted (Issue 507) ────────


def test_the_sweep_finds_the_known_render_tasks() -> None:
    """Anti-vacuity: a sweep that discovers nothing would pass everything below.

    This is the arm the old literal-vs-itself assertion never had. If the call
    graph breaks — an import style it cannot resolve, a rename, a refactor that
    moves the encode behind an indirection — the counts collapse and this fails
    before the routing assertions can pass for the wrong reason.
    """
    discovered = discover_ffmpeg_reaching_tasks()
    assert len(discovered) >= 8, (
        f"call-graph sweep found only {len(discovered)} ffmpeg-reaching task(s) — "
        "it has almost certainly stopped resolving something. Known good: 8."
    )
    for known in RENDER_TASKS:
        assert known in discovered, (
            f"{known} routes to the render queue but the sweep cannot see it reach "
            "ffmpeg — the call graph is broken, not the routing"
        )


def test_every_ffmpeg_task_is_routed_or_exempted() -> None:
    """Every task that spawns ffmpeg in-process is routed, or says why not.

    Replaces a literal compared to a copy of itself. The population comes from
    the call graph, so a new ffmpeg task lands here on the day it is written.
    """
    discovered = discover_ffmpeg_reaching_tasks()
    unaccounted = discovered - set(RENDER_TASKS) - set(_ROUTING_EXEMPTIONS)
    assert not unaccounted, (
        f"ffmpeg-reaching task(s) neither routed nor exempted: {sorted(unaccounted)}. "
        f"Add to RENDER_TASKS in worker/celery_app.py, or to _ROUTING_EXEMPTIONS "
        "here with a written reason."
    )


def test_no_stale_routing_exemption() -> None:
    """The reverse arm: an exemption for a task that no longer touches ffmpeg.

    Without this, a rename or a removed ffmpeg call leaves the registry quietly
    covering a ghost — the drift that made every literal in this repo untrustworthy.
    """
    discovered = discover_ffmpeg_reaching_tasks()
    stale = set(_ROUTING_EXEMPTIONS) - discovered
    assert not stale, (
        f"stale exemption(s) — these tasks no longer reach ffmpeg: {sorted(stale)}. "
        "Remove the entry rather than leaving it to cover a ghost."
    )


def test_every_exemption_carries_a_reason() -> None:
    """Membership is not evidence. An exemption must state WHY, in prose."""
    for name, reason in _ROUTING_EXEMPTIONS.items():
        assert len(reason.strip()) >= 60, (
            f"{name}'s exemption reason is too thin to be a decision: {reason!r}"
        )


def test_routed_tasks_all_actually_reach_ffmpeg() -> None:
    """A routed task that no longer encodes is needlessly serialized."""
    discovered = discover_ffmpeg_reaching_tasks()
    pointless = set(RENDER_TASKS) - discovered
    assert not pointless, (
        f"routed to the concurrency-1 render queue but no longer reaches ffmpeg: "
        f"{sorted(pointless)} — they are being serialized for nothing"
    )
