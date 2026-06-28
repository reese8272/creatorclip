"""Issue 332 — every LLM module must emit the Prometheus token metric.

``record_llm_tokens`` (and its dual-shape adapter ``record_llm_metric``) increment
``llm_tokens_total`` so Grafana can break LLM spend down by feature/model. The metric
was only wired into ~half the call sites, leaving the heaviest consumers (scoring,
DNA brief, most knowledge features) invisible on the cost dashboard while billing
(``record_llm_usage``) was complete. This guard mirrors ``test_usage_coverage.py``:
it asserts each LLM module references the metric call, so the dashboard can't silently
go blind again on a refactor.

Runs in the default unit lane — no app import, no DB/Redis.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent

# Every module that runs Anthropic inference and therefore must record the token
# metric. Verified against `grep -rl messages.create / stream_and_emit` (Issue 332).
_LLM_MODULES = [
    "clip_engine/scoring.py",
    "dna/brief.py",
    "analysis/brief.py",
    "improvement/brief.py",
    "knowledge/titles.py",
    "knowledge/thumbnails.py",
    "knowledge/chapters.py",
    "knowledge/hooks.py",
    "knowledge/clip_titles.py",
    "knowledge/clip_captions.py",
    "knowledge/clip_explain.py",
    "chat/runner.py",
    "chat/intake.py",
]

# Either the direct counter or the dual-shape adapter satisfies the requirement.
_METRIC_CALLS = ("record_llm_metric(", "record_llm_tokens(")


@pytest.mark.parametrize("rel_path", _LLM_MODULES)
def test_llm_module_records_token_metric(rel_path: str) -> None:
    src = (_REPO_ROOT / rel_path).read_text(encoding="utf-8")
    assert any(call in src for call in _METRIC_CALLS), (
        f"{rel_path} runs LLM inference but never calls record_llm_metric / "
        "record_llm_tokens, so its tokens are missing from the llm_tokens_total "
        "metric (cost-by-feature dashboard goes blind). Add "
        "'from observability import record_llm_metric' and call "
        "'record_llm_metric(<model>, <usage>)' next to the billing-ledger write."
    )
