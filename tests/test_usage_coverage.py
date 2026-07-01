"""Issue 321 — AST-based guard: every LLM Celery task helper must call record_llm_usage.

``record_llm_usage`` (billing/ledger.py) is the billing ledger write that deducts
from the creator's token/minute pack.  If a task runs inference without calling it,
creators get LLM calls that are unmetered — a real revenue leak at scale.

This test does NOT import the app, does NOT need DB/Redis, and runs in the default
unit lane.  It finds each LLM async helper in worker/tasks.py by name and asserts
that ``record_llm_usage`` appears in the source lines for that function body.
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
