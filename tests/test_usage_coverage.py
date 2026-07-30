"""Issue 321 + w2/billing-audit — AST guards: every LLM call site must be billed.

``record_llm_usage`` (billing/ledger.py) is the billing ledger write that deducts
from the creator's token/minute pack.  If a task runs inference without calling it,
creators get LLM calls that are unmetered — a real revenue leak at scale.

Two layers, both in the default unit lane (no app import, no DB/Redis):

1. Issue 321: each LLM async helper in worker/tasks.py must call
   ``record_llm_usage`` inside its own body.
2. w2/billing-audit: a repo-wide sweep — every function that invokes the
   Anthropic client (``*.messages.create`` / ``*.messages.stream``) or a stream
   transport helper must appear in ``_ANTHROPIC_CALL_SITES`` mapped to concrete
   billing evidence.  The 2026-07-29 audit found two entirely-unbilled sites
   (chat intake, thumbnail-patterns vision) that layer 1 could not see; this
   layer fails CI on the NEXT unbilled site.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_TASKS_FILE = _REPO_ROOT / "worker" / "tasks.py"

# LLM async helpers in worker/tasks.py — these all call into Anthropic and MUST
# record usage so the billing ledger is accurate.
# Verified against record_llm_usage grep output (Issue 321 build, 2026-06-26).
_LLM_ASYNC_HELPERS = [
    "_build_dna_async",
    "_generate_improvement_brief_async",
    "_generate_video_analysis_async",
    "_generate_title_suggestions_async",
    "_generate_thumbnail_concepts_async",
    "_analyze_hook_async",
    "_generate_chapters_async",
    "_distill_style_prefs_async",
]


def _extract_function_source(tasks_src: str, func_name: str) -> str | None:
    """Return the source lines belonging to ``func_name`` in tasks_src.

    Uses AST to find the function's start and end line numbers, then slices
    the source so we don't false-positive on a nearby function that also
    calls record_llm_usage.
    """
    tree = ast.parse(tasks_src)
    src_lines = tasks_src.splitlines()

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            # ast gives lineno (1-based) and end_lineno (1-based, inclusive)
            start = node.lineno - 1  # 0-indexed
            end = (node.end_lineno or node.lineno) - 1  # 0-indexed, inclusive
            return "\n".join(src_lines[start : end + 1])

    return None  # function not found


@pytest.fixture(scope="module")
def tasks_src() -> str:
    return _TASKS_FILE.read_text(encoding="utf-8")


@pytest.mark.parametrize("func_name", _LLM_ASYNC_HELPERS)
def test_llm_task_calls_record_llm_usage(tasks_src: str, func_name: str) -> None:
    """LLM task helper must call record_llm_usage inside its own body.

    An absent call means the task runs inference without metering — a billing
    leak.  Fix: import from billing.ledger and call ``await record_llm_usage(...)``
    after each inference call inside the function.
    """
    body = _extract_function_source(tasks_src, func_name)
    assert body is not None, (
        f"Function '{func_name}' not found in worker/tasks.py. "
        "If it was renamed, update _LLM_ASYNC_HELPERS in this test."
    )
    assert "record_llm_usage" in body, (
        f"'{func_name}' in worker/tasks.py does NOT call record_llm_usage. "
        "Add 'from billing.ledger import record_llm_usage' inside the function and "
        "call 'await record_llm_usage(...)' after every Anthropic inference call. "
        "Missing this causes unmetered LLM usage — a real billing leak."
    )


# ── Layer 2 (w2/billing-audit): repo-wide Anthropic call-site → billing map ───

# Directories that never contain billable app code.
_SWEEP_EXCLUDE_DIRS = {
    "tests",
    "scripts",
    ".venv",
    "alembic",
    "migrations",
    ".claude",
    "static",
    "frontend",
    "deploy",
    "docs",
    "node_modules",
    ".git",
}

# Shared transport helpers (worker/anthropic_stream.py). Calling one of these IS
# an LLM call — the caller is a billable site; the helpers themselves only relay
# usage back and are exempt (empty evidence list below).
_TRANSPORT_HELPERS = {"stream_and_emit", "stream_message", "stream_until_final"}

# Every known Anthropic call site, mapped to its billing evidence:
#   (call-site file, enclosing function) -> [(billing file, billing function, marker), ...]
# The marker must appear in the billing function's source.  ``record_llm_usage``
# is the standard choke point (feeds the ledger + spend guard + cost metric);
# scoring/chat bill in-function via ``_estimate_cost_usd`` + increment_usage.
# An EMPTY list marks a transport helper (usage returned to callers, which are
# themselves entries here).  A NEW unlisted call site fails the sweep below —
# add billing first, then extend this map.
_ANTHROPIC_CALL_SITES: dict[tuple[str, str], list[tuple[str, str, str]]] = {
    # Transport helpers — exempt (relay usage to their callers).
    ("worker/anthropic_stream.py", "stream_and_emit"): [],
    ("worker/anthropic_stream.py", "stream_message"): [],
    ("worker/anthropic_stream.py", "stream_until_final"): [],
    # Bill in their own body.
    ("chat/intake.py", "run_intake_turn"): [
        ("chat/intake.py", "run_intake_turn", "record_llm_usage"),
    ],
    ("chat/runner.py", "run_chat_turn"): [
        ("chat/runner.py", "run_chat_turn", "_estimate_cost_usd"),
    ],
    ("clip_engine/scoring.py", "score_candidates"): [
        ("clip_engine/scoring.py", "score_candidates", "_estimate_cost_usd"),
    ],
    ("routers/insights.py", "analyze_performer"): [
        ("routers/insights.py", "analyze_performer", "record_llm_usage"),
    ],
    # Producers billed by their consumers.
    ("dna/brief.py", "generate_brief"): [
        ("worker/tasks.py", "_build_dna_async", "record_llm_usage"),
    ],
    # Issue 371 — style-preference distillation, billed in the worker task.
    ("preference/style_distill.py", "distill_style_notes"): [
        ("worker/tasks.py", "_distill_style_prefs_async", "record_llm_usage"),
    ],
    ("improvement/brief.py", "generate_improvement_brief"): [
        ("worker/tasks.py", "_generate_improvement_brief_async", "record_llm_usage"),
    ],
    ("analysis/brief.py", "generate_video_analysis"): [
        ("worker/tasks.py", "_generate_video_analysis_async", "record_llm_usage"),
    ],
    ("knowledge/titles.py", "generate_title_suggestions"): [
        ("worker/tasks.py", "_generate_title_suggestions_async", "record_llm_usage"),
    ],
    ("knowledge/hooks.py", "analyze_hook"): [
        ("worker/tasks.py", "_analyze_hook_async", "record_llm_usage"),
    ],
    ("knowledge/chapters.py", "generate_chapters"): [
        ("worker/tasks.py", "_generate_chapters_async", "record_llm_usage"),
    ],
    ("knowledge/thumbnails.py", "generate_thumbnail_concepts"): [
        ("worker/tasks.py", "_generate_thumbnail_concepts_async", "record_llm_usage"),
    ],
    # Vision call with TWO compute paths — each must bill the patterns usage
    # (unbilled until w2/billing-audit 2026-07-29).  The worker marker pins the
    # specific usage variable so the concepts billing line cannot satisfy it.
    ("knowledge/thumbnails.py", "analyze_thumbnail_patterns"): [
        ("worker/tasks.py", "_generate_thumbnail_concepts_async", "_patterns_usage,"),
        ("routers/thumbnails.py", "get_thumbnail_patterns", "record_llm_usage"),
    ],
    # Two callers, each billing its own path (the chat-tool caller discarded
    # usage until the 2026-07-29 assess fix).  Note: the sweep discovers LLM
    # call sites (messages.create/stream + transport helpers), not callers of
    # usage-returning producers — a NEW caller of a producer must be added
    # here manually with its billing evidence.
    ("knowledge/clip_titles.py", "generate_clip_title_suggestions"): [
        ("routers/clips.py", "get_clip_title_suggestions", "record_llm_usage"),
        ("chat/tools.py", "_suggest_clip_titles", "record_llm_usage"),
    ],
    ("knowledge/clip_captions.py", "generate_clip_caption_hooks"): [
        ("routers/clips.py", "get_clip_caption_hooks", "record_llm_usage"),
    ],
    ("knowledge/clip_explain.py", "generate_clip_explanation"): [
        ("routers/clips.py", "get_clip_explanation", "record_llm_usage"),
    ],
}


class _CallSiteVisitor(ast.NodeVisitor):
    """Records (relpath, top-level enclosing function) for every LLM call.

    Detects ``<anything>.messages.create(...)`` / ``<anything>.messages.stream(...)``
    plus calls to the named transport helpers.  Attribution is to the TOP-level
    enclosing function so nested closures map to their route/task.
    """

    def __init__(self, rel: str, sites: set[tuple[str, str]]) -> None:
        self.rel = rel
        self.sites = sites
        self.stack: list[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        is_messages_api = (
            isinstance(func, ast.Attribute)
            and func.attr in {"create", "stream"}
            and isinstance(func.value, ast.Attribute)
            and func.value.attr == "messages"
        )
        is_transport = (isinstance(func, ast.Attribute) and func.attr in _TRANSPORT_HELPERS) or (
            isinstance(func, ast.Name) and func.id in _TRANSPORT_HELPERS
        )
        if is_messages_api or is_transport:
            self.sites.add((self.rel, self.stack[0] if self.stack else "<module>"))
        self.generic_visit(node)


def _discover_anthropic_call_sites() -> set[tuple[str, str]]:
    """AST sweep of the repo for LLM call sites (see ``_CallSiteVisitor``)."""
    sites: set[tuple[str, str]] = set()

    for path in sorted(_REPO_ROOT.rglob("*.py")):
        rel = path.relative_to(_REPO_ROOT)
        if any(part in _SWEEP_EXCLUDE_DIRS for part in rel.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover — repo must parse; be loud elsewhere
            continue
        _CallSiteVisitor(str(rel), sites).visit(tree)

    return sites


def test_every_anthropic_call_site_is_mapped_to_billing() -> None:
    """Repo-wide sweep: no Anthropic call site may exist outside the billing map.

    A failure here means an LLM call was added (or moved) without billing.  Fix:
    bill it through ``billing.ledger.record_llm_usage`` (the choke point feeding
    the ledger, the Issue-290 spend guard, and the cost metric), THEN add the
    site + evidence to ``_ANTHROPIC_CALL_SITES``.
    """
    discovered = _discover_anthropic_call_sites()
    mapped = set(_ANTHROPIC_CALL_SITES)

    unmapped = discovered - mapped
    assert not unmapped, (
        f"Unbilled/unmapped Anthropic call site(s): {sorted(unmapped)}. "
        "Every *.messages.create/stream or stream-helper call must be billed via "
        "billing.ledger.record_llm_usage and registered in _ANTHROPIC_CALL_SITES "
        "with its billing evidence. Unmapped sites are 0x-billed revenue leaks "
        "invisible to the spend guard (see chat/intake + thumbnail-patterns, "
        "fixed 2026-07-29)."
    )

    stale = mapped - discovered
    assert not stale, (
        f"Stale _ANTHROPIC_CALL_SITES entr(ies): {sorted(stale)}. "
        "The call site no longer exists (renamed/removed) — update the map so it "
        "stays exact."
    )


@pytest.mark.parametrize(
    "site,evidence",
    [(site, ev) for site, evs in _ANTHROPIC_CALL_SITES.items() for ev in evs],
    ids=lambda v: "/".join(v) if isinstance(v, tuple) and len(v) == 2 else None,
)
def test_billing_evidence_present(site: tuple[str, str], evidence: tuple[str, str, str]) -> None:
    """Each mapped call site's declared billing function must contain its marker."""
    bill_file, bill_func, marker = evidence
    src = (_REPO_ROOT / bill_file).read_text(encoding="utf-8")
    body = _extract_function_source(src, bill_func)
    assert body is not None, (
        f"Billing function '{bill_func}' not found in {bill_file} "
        f"(declared as billing evidence for {site}). Update _ANTHROPIC_CALL_SITES."
    )
    assert marker in body, (
        f"{bill_file}::{bill_func} no longer contains {marker!r} — the LLM call at "
        f"{site} appears to have lost its billing write. Restore the "
        "record_llm_usage/_estimate_cost_usd call or re-point the evidence."
    )
