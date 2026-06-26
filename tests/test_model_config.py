"""Issue 318 — per-task model config registry.

Two test strategies:
  (A) grep-scan the source tree for hardcoded model-name 'claude-' string literals
      (quoted in code) outside config.py and tests/ — asserts zero hits, making the
      ban regression-proof.
  (B) enumerate every per-task model key from settings and assert each resolves
      to a non-empty string matching the bare claude-* alias pattern.
"""

import re
from pathlib import Path

import pytest

# All per-task model keys added in Issue 318
_TASK_MODEL_KEYS = [
    "ANTHROPIC_MODEL_SCORING",
    "ANTHROPIC_MODEL_DNA_BRIEF",
    "ANTHROPIC_MODEL_ANALYSIS",
    "ANTHROPIC_MODEL_TITLES",
    "ANTHROPIC_MODEL_THUMBNAILS",
    "ANTHROPIC_MODEL_HOOKS",
    "ANTHROPIC_MODEL_CHAPTERS",
    "ANTHROPIC_MODEL_PERFORMER",
    "ANTHROPIC_MODEL_CHAT",
    "ANTHROPIC_MODEL_INTAKE",
    "ANTHROPIC_MODEL_IMPROVEMENT",
]

# Bare alias pattern for Claude model names: claude-<family>-<major>-<minor>
# Examples: claude-sonnet-4-6, claude-haiku-4-5, claude-opus-4-5
# Source: https://platform.claude.com/docs/en/about-claude/models/overview (2026-06-26)
_BARE_ALIAS_RE = re.compile(r"^claude-[a-z]+-[0-9]+-[0-9]+$")

# Date-suffix pattern to explicitly reject: e.g. claude-haiku-4-5-20251001
_DATE_SUFFIX_RE = re.compile(r"^claude-[a-z]+-[0-9]+-[0-9]+-[0-9]{8}$")

# Quoted model string pattern: a claude model ID inside a Python string literal.
# Matches 'claude-haiku-4-5' or "claude-sonnet-4-6" etc.  Only detects the
# model-family strings (haiku/sonnet/opus) so docstring references to /claude-api
# (the skill path) are NOT flagged.
_QUOTED_MODEL_RE = re.compile(r'["\']claude-(?:haiku|sonnet|opus|claude)-[0-9]')


def _repo_root() -> Path:
    """Return the repository root (the directory containing config.py)."""
    return Path(__file__).parent.parent


def _python_source_files() -> list[Path]:
    """Collect all .py files outside tests/ and the worktree-internal .claude/ tree."""
    root = _repo_root()
    excluded_dirs = {"tests", ".claude", "__pycache__", ".git", "node_modules", "frontend"}
    files = []
    for path in root.rglob("*.py"):
        parts = set(path.relative_to(root).parts)
        if not parts & excluded_dirs:
            files.append(path)
    return files


def test_no_claude_model_literals_outside_config_and_tests() -> None:
    """No hardcoded Claude model-ID literals in source outside config.py and tests/.

    We scan for quoted model strings (e.g. 'claude-haiku-4-5-20251001') in code,
    excluding config.py (the canonical location) and tests/ (allowed for fixtures).
    References to /claude-api (the skill path) and comment-only occurrences are
    excluded by the pattern.
    """
    offenders: list[str] = []
    for path in _python_source_files():
        if path.name == "config.py":
            continue
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            # Strip inline comment so "# 'claude-haiku-4-5'" in a comment is excluded
            code_part = line.split("#")[0]
            if _QUOTED_MODEL_RE.search(code_part):
                offenders.append(f"{path}:{lineno}: {line.strip()}")

    assert offenders == [], (
        "Hardcoded Claude model-ID literals found outside config.py/tests/:\n"
        + "\n".join(offenders)
        + "\nFix: replace with the appropriate settings.ANTHROPIC_MODEL_<TASK> key."
    )


def test_all_task_model_keys_resolve() -> None:
    """Every ANTHROPIC_MODEL_<TASK> key in settings resolves to a non-empty bare alias."""
    from config import settings

    for key in _TASK_MODEL_KEYS:
        value = getattr(settings, key)
        assert value, f"settings.{key} is empty"
        assert isinstance(value, str), f"settings.{key} is not a str"
        assert _BARE_ALIAS_RE.match(value), (
            f"settings.{key}={value!r} does not match bare alias pattern "
            f"(e.g. 'claude-sonnet-4-6' or 'claude-haiku-4-5'). "
            f"Date-suffixed strings like 'claude-haiku-4-5-20251001' are not allowed."
        )
        assert not _DATE_SUFFIX_RE.match(value), (
            f"settings.{key}={value!r} looks like a date-suffixed ID. "
            f"Use a bare alias instead (no date suffix)."
        )


def test_default_models_are_expected_tier() -> None:
    """Verify the default routing: Haiku for cheap classify, Sonnet for reasoning."""
    from config import settings

    haiku_tasks = {"ANTHROPIC_MODEL_HOOKS", "ANTHROPIC_MODEL_CHAPTERS", "ANTHROPIC_MODEL_PERFORMER"}
    sonnet_tasks = set(_TASK_MODEL_KEYS) - haiku_tasks

    for key in haiku_tasks:
        value = getattr(settings, key)
        assert "haiku" in value.lower(), (
            f"settings.{key}={value!r} should default to a Haiku model "
            f"(cheap classify task)."
        )

    for key in sonnet_tasks:
        value = getattr(settings, key)
        assert "sonnet" in value.lower(), (
            f"settings.{key}={value!r} should default to a Sonnet model "
            f"(reasoning/streaming task)."
        )
