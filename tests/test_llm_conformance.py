"""Issue 320 — Anthropic-SDK production-standards conformance test.

Tests conformance of every LLM module against four production standards:
  1. Module-level singleton with timeout + max_retries set
  2. No hardcoded model literals (delegated to test_model_config.py — not repeated here)
  3. Typed exception handling: at least one of RateLimitError/APIStatusError/APIConnectionError
     appears in the module source as an import OR in an except clause
  4. UNTRUSTED_CONTENT_POLICY injection in modules that accept user-supplied strings
  5. Cache-control breakpoints presence/absence per module intent (documented in the test)

All checks run in the default unit lane: no live API, no DB, no Docker needed.

Cache floor source:
  Sonnet 4.6: minimum cacheable prefix = 1,024 tokens
  Haiku  4.5: minimum cacheable prefix = 4,096 tokens
  Source: https://platform.claude.com/docs/en/build-with-claude/prompt-caching (fetched 2026-06-26)
"""

from __future__ import annotations

import ast
import importlib
import types
from pathlib import Path

import pytest

# ── Module registry ───────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).parent.parent

# (import_path, singleton_name, is_async)
_LLM_MODULES: list[tuple[str, str, bool]] = [
    ("knowledge.hooks", "_ANTHROPIC", False),
    ("knowledge.titles", "_ANTHROPIC", False),
    ("knowledge.chapters", "_ANTHROPIC", False),
    ("knowledge.thumbnails", "_ANTHROPIC", False),
    ("analysis.brief", "_ANTHROPIC", False),
    ("dna.brief", "_ANTHROPIC", False),
    ("improvement.brief", "_ANTHROPIC", False),
    ("chat.runner", "_ANTHROPIC", False),
    ("chat.intake", "_ANTHROPIC", True),
    ("clip_engine.scoring", "_ANTHROPIC", True),
]

# Modules whose system prompts MUST contain UNTRUSTED_CONTENT_POLICY.
# Only includes modules that accept user-supplied transcript / title / description.
_MUST_INJECT_POLICY = {
    "knowledge.hooks",
    "knowledge.titles",
    "knowledge.thumbnails",
    "clip_engine.scoring",
    "chat.intake",
}

# Modules that SHOULD have a cache_control breakpoint in their system prompt
# (because their prefix clears the relevant model's cacheable floor).
_MUST_CACHE = {
    "knowledge.titles",     # Block 2 DNA brief, ttl=1h, Sonnet 4.6 1024-token floor
    "knowledge.thumbnails", # Block 2 DNA brief, ttl=1h, Sonnet 4.6 1024-token floor
    "clip_engine.scoring",  # Block 2 DNA brief, ttl=1h, Sonnet 4.6 1024-token floor
    "improvement.brief",    # cache_control ephemeral on static instructions block
}

# Modules that have CONFIRMED NO cache_control (prefix too short for their model floor)
# — verified in the Issue-135 audit and Issue-315 cleanup.
# Having an explicit "absent" assertion documents the intentional decision.
_MUST_NOT_CACHE = {
    "knowledge.hooks",    # Haiku 4.5, prefix ~900 tokens — below 4096 floor
    "knowledge.chapters", # Haiku 4.5, prefix ~175 tokens — below 4096 floor
}

# Typed error names the conformance check accepts as "typed handling present"
_TYPED_ERRORS = {"RateLimitError", "APIStatusError", "APIConnectionError"}


def _source_path(import_path: str) -> Path:
    parts = import_path.replace(".", "/")
    return _REPO_ROOT / f"{parts}.py"


def _read_source(import_path: str) -> str:
    return _source_path(import_path).read_text(encoding="utf-8")


def _parse_ast(import_path: str) -> ast.Module:
    return ast.parse(_read_source(import_path))


# ── 1. Singleton assertion ─────────────────────────────────────────────────────

@pytest.mark.parametrize("mod_path,singleton_name,_is_async", _LLM_MODULES)
def test_singleton_has_timeout_and_max_retries(
    mod_path: str, singleton_name: str, _is_async: bool
) -> None:
    """Module-level Anthropic singleton must have timeout and max_retries set."""
    # Import the module and inspect the singleton object at runtime
    module = importlib.import_module(mod_path)
    singleton = getattr(module, singleton_name, None)
    assert singleton is not None, (
        f"{mod_path}.{singleton_name} is None — module-level singleton missing"
    )

    # timeout is set on the httpx client inside the Anthropic SDK
    timeout = getattr(singleton, "timeout", None)
    assert timeout is not None, (
        f"{mod_path}.{singleton_name}.timeout is None; pass httpx.Timeout(...) to constructor"
    )

    # max_retries must be >= 1 for transient error recovery
    max_retries = getattr(singleton, "max_retries", None)
    assert max_retries is not None and max_retries >= 1, (
        f"{mod_path}.{singleton_name}.max_retries={max_retries!r}; must be >= 1"
    )


# ── 2. Typed exception handling ────────────────────────────────────────────────

@pytest.mark.parametrize("mod_path,_,__", _LLM_MODULES)
def test_typed_exception_handling_present(mod_path: str, _: str, __: bool) -> None:
    """Each LLM module must import or except at least one typed Anthropic error.

    Conformance: import any of RateLimitError, APIStatusError, APIConnectionError.
    This ensures typed errors can be caught (not just propagated as bare Exception).
    """
    source = _read_source(mod_path)
    found = any(err in source for err in _TYPED_ERRORS)
    assert found, (
        f"{mod_path}: no typed Anthropic exception found. "
        f"Add 'from anthropic import RateLimitError, APIStatusError, APIConnectionError' "
        f"and wrap messages.create / stream_and_emit calls. "
        f"Accepted names: {_TYPED_ERRORS}"
    )


# ── 3. UNTRUSTED_CONTENT_POLICY injection ─────────────────────────────────────

@pytest.mark.parametrize(
    "mod_path",
    sorted(_MUST_INJECT_POLICY),
)
def test_untrusted_content_policy_present(mod_path: str) -> None:
    """Modules that accept user-supplied content must inject UNTRUSTED_CONTENT_POLICY."""
    source = _read_source(mod_path)
    assert "UNTRUSTED_CONTENT_POLICY" in source, (
        f"{mod_path}: UNTRUSTED_CONTENT_POLICY not found in source. "
        "User-supplied transcript/title/description is an injection surface (OWASP LLM01). "
        "Import and inject UNTRUSTED_CONTENT_POLICY from knowledge.util."
    )


# ── 4. Cache-control breakpoint presence ──────────────────────────────────────

@pytest.mark.parametrize("mod_path", sorted(_MUST_CACHE))
def test_cache_control_present(mod_path: str) -> None:
    """Modules with a long-enough prefix must have a cache_control breakpoint.

    Sonnet 4.6 cacheable floor: 1,024 tokens.
    Haiku  4.5 cacheable floor: 4,096 tokens.
    Source: https://platform.claude.com/docs/en/build-with-claude/prompt-caching (2026-06-26)

    We check for the dict-key form (``"cache_control"``) rather than bare text
    to avoid false-positives from comment mentions like ``# cache_control breakpoint``.
    """
    source = _read_source(mod_path)
    assert '"cache_control"' in source, (
        f'{mod_path}: "cache_control" dict key not found in code. '
        "This module's prefix clears the cacheable floor — add a cache_control breakpoint "
        'e.g. {"cache_control": {"type": "ephemeral"}} so the 2nd same-creator call '
        "benefits from cache reads."
    )


@pytest.mark.parametrize("mod_path", sorted(_MUST_NOT_CACHE))
def test_cache_control_intentionally_absent(mod_path: str) -> None:
    """Modules with prefix below cacheable floor must NOT have cache_control dict key.

    Paying the write premium (1.25× or 2×) for a prefix that cannot cache is
    waste. The Issue-135 audit and Issue-315 cleanup confirmed these modules'
    prefixes are below the relevant model floor and markers were removed.

    We check for the dict-key form (``"cache_control"``) so comment-only mentions
    (e.g. ``# cache_control breakpoint removed``) do NOT trigger a false failure.
    """
    source = _read_source(mod_path)
    assert '"cache_control"' not in source, (
        f'{mod_path}: "cache_control" dict key found in code, but the prefix is below '
        "the cacheable floor (Haiku 4.5 requires 4,096 tokens minimum). Remove the "
        "cache_control marker to avoid paying the write premium for a prefix that cannot cache."
    )
