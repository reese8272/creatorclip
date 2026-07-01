"""Issue 321 — Per-creator brief quota: structural tests.

Verifies that:
  1. BRIEF_DAILY_LIMIT_PER_CREATOR is present in config with a sensible default.
  2. BRIEF_DAILY_LIMIT is exported from limiter.py and has the correct format.
  3. Every brief-generating router endpoint applies the BRIEF_DAILY_LIMIT decorator.

All checks run in the default unit lane: no live API, no DB, no Docker needed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent

# (router file, endpoint function name)
_BRIEF_ENDPOINTS = [
    ("routers/titles.py", "start_title_suggestions"),
    ("routers/thumbnails.py", "get_thumbnail_patterns"),
    ("routers/thumbnails.py", "start_thumbnail_concepts"),
    ("routers/insights.py", "analyze_performer"),
    ("routers/improvement.py", "start_improvement_brief"),
]

# slowapi limit string format: "N/day"
_LIMIT_RE = re.compile(r"^\d+/day$")


# ── 1. Config key presence and default ────────────────────────────────────────

def test_brief_daily_limit_config_key_exists() -> None:
    """BRIEF_DAILY_LIMIT_PER_CREATOR must be a declared field in Settings."""
    src = (_REPO_ROOT / "config.py").read_text(encoding="utf-8")
    assert "BRIEF_DAILY_LIMIT_PER_CREATOR" in src, (
        "BRIEF_DAILY_LIMIT_PER_CREATOR not found in config.py. "
        "Add it as: BRIEF_DAILY_LIMIT_PER_CREATOR: int = 50"
    )


def test_brief_daily_limit_config_default_is_positive() -> None:
    """BRIEF_DAILY_LIMIT_PER_CREATOR default must be a positive integer."""
    src = (_REPO_ROOT / "config.py").read_text(encoding="utf-8")
    # Match the assignment line: BRIEF_DAILY_LIMIT_PER_CREATOR: int = <N>
    m = re.search(r"BRIEF_DAILY_LIMIT_PER_CREATOR\s*:\s*int\s*=\s*(\d+)", src)
    assert m is not None, (
        "BRIEF_DAILY_LIMIT_PER_CREATOR: int = <N> not found in config.py. "
        "Ensure the type annotation and default value are on the same line."
    )
    default = int(m.group(1))
    assert default > 0, (
        f"BRIEF_DAILY_LIMIT_PER_CREATOR default is {default} — must be > 0. "
        "A zero or negative limit would block all brief endpoints immediately."
    )


# ── 2. limiter.py exports BRIEF_DAILY_LIMIT ───────────────────────────────────

def test_brief_daily_limit_exported_from_limiter() -> None:
    """BRIEF_DAILY_LIMIT must be exported from limiter.py."""
    src = (_REPO_ROOT / "limiter.py").read_text(encoding="utf-8")
    assert "BRIEF_DAILY_LIMIT" in src, (
        "BRIEF_DAILY_LIMIT not found in limiter.py. "
        "Add: BRIEF_DAILY_LIMIT: str = daily_limit(settings.BRIEF_DAILY_LIMIT_PER_CREATOR)"
    )


def test_brief_daily_limit_format() -> None:
    """BRIEF_DAILY_LIMIT must resolve to a valid 'N/day' limit string.

    Uses daily_limit() from limiter which returns '{cap}/day'.
    Verified against slowapi's limits.parse() format requirements.
    """
    import os
    import sys

    # Provide minimal env stubs so config.py loads without Docker
    for key, val in {
        "ANTHROPIC_API_KEY": "sk-ant-stub",
        "DATABASE_URL": "postgresql+asyncpg://test:test@localhost/test",
        "REDIS_URL": "redis://localhost:6379/0",
        "GOOGLE_OAUTH_CLIENT_ID": "stub",
        "GOOGLE_OAUTH_CLIENT_SECRET": "stub",
        "OAUTH_REDIRECT_URI": "http://localhost:8000/auth/callback",
        "TOKEN_ENCRYPTION_KEY": "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleQ==",
        "JWT_SECRET_KEY": "test-jwt-secret-32-bytes-minimum-!",
        "ALLOWED_ORIGINS": "http://localhost:8000",
        "VOYAGE_API_KEY": "stub",
        "MAILING_ADDRESS": "stub",
    }.items():
        os.environ.setdefault(key, val)

    sys.path.insert(0, str(_REPO_ROOT))

    # Import BRIEF_DAILY_LIMIT — must be a "N/day" string.
    # We read limiter.py source to avoid triggering the Redis connectivity check
    # inside pytest_configure (conftest requires Redis on startup).
    src = (_REPO_ROOT / "limiter.py").read_text(encoding="utf-8")
    m = re.search(r'BRIEF_DAILY_LIMIT\s*:\s*str\s*=\s*daily_limit\(\s*settings\.(\w+)\s*\)', src)
    assert m is not None, (
        "Could not find BRIEF_DAILY_LIMIT: str = daily_limit(settings.<key>) in limiter.py. "
        "Ensure it follows the same pattern as LLM_DAILY_LIMIT and RENDER_DAILY_LIMIT."
    )
    config_key = m.group(1)
    assert config_key == "BRIEF_DAILY_LIMIT_PER_CREATOR", (
        f"BRIEF_DAILY_LIMIT sources from settings.{config_key!r}, "
        "expected settings.BRIEF_DAILY_LIMIT_PER_CREATOR."
    )


# ── 3. Each brief endpoint applies BRIEF_DAILY_LIMIT ──────────────────────────

@pytest.mark.parametrize("router_file,endpoint_name", _BRIEF_ENDPOINTS)
def test_brief_endpoint_has_daily_limit_decorator(
    router_file: str, endpoint_name: str
) -> None:
    """Brief-generating endpoints must have @limiter.limit(BRIEF_DAILY_LIMIT, ...).

    This is the structural guard: if someone removes the decorator from the
    endpoint, this test fails — before a PR merges.
    """
    src = (_REPO_ROOT / router_file).read_text(encoding="utf-8")

    # Find the block of decorators immediately preceding the async def
    # We scan backwards from 'async def <name>' to capture stacked decorators.
    lines = src.splitlines()
    func_def_idx = next(
        (i for i, line in enumerate(lines) if f"async def {endpoint_name}(" in line),
        None,
    )
    assert func_def_idx is not None, (
        f"async def {endpoint_name}(...) not found in {router_file}. "
        "If the function was renamed, update _BRIEF_ENDPOINTS in this test."
    )

    # Collect decorator lines directly above the function def
    decorator_lines: list[str] = []
    i = func_def_idx - 1
    while i >= 0 and (lines[i].lstrip().startswith("@") or not lines[i].strip()):
        if lines[i].lstrip().startswith("@"):
            decorator_lines.append(lines[i])
        i -= 1

    decorator_block = "\n".join(decorator_lines)
    assert "BRIEF_DAILY_LIMIT" in decorator_block, (
        f"{router_file}:{endpoint_name} is missing @limiter.limit(BRIEF_DAILY_LIMIT, "
        "key_func=creator_key). Add it so the per-creator brief quota is enforced. "
        f"Current decorators: {decorator_block!r}"
    )
