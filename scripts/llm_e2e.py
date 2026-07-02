#!/usr/bin/env python3.12
"""End-to-end live-API LLM verification harness (Issue 319).

Exercises all LLM modules against the REAL Anthropic API (not mocked) to
verify: schema-valid output, honesty disclaimer present, prompt cache landing,
usage recording, and typed-exception propagation.

Usage:
    RUN_LLM_LIVE=1 ANTHROPIC_API_KEY=sk-... python scripts/llm_e2e.py

Environment requirements:
    RUN_LLM_LIVE=1         — guard; harness exits 0 immediately if unset
    ANTHROPIC_API_KEY      — real key

All other env vars default to test stubs so no DB/Redis is needed.

Exit codes: 0 = all assertions pass, 1 = one or more assertions failed.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import sys
import uuid
from pathlib import Path

# ── Make the repo root importable when run as `python scripts/llm_e2e.py` ─────
# Running a script puts its own dir (scripts/) on sys.path[0], NOT the repo root,
# so `import knowledge` / `config` would fail. Prepend the repo root explicitly
# so the documented standalone invocation works without PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ── Bootstrap environment stubs BEFORE any app import ─────────────────────────
_FERNET_STUB = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
_STUBS: dict[str, str] = {
    "DATABASE_URL": "postgresql+psycopg://creatorclip:dev@localhost:5432/creatorclip",
    "REDIS_URL": "redis://localhost:6379/0",
    "GOOGLE_OAUTH_CLIENT_ID": "stub",
    "GOOGLE_OAUTH_CLIENT_SECRET": "stub",
    "OAUTH_REDIRECT_URI": "http://localhost:8000/auth/callback",
    "TOKEN_ENCRYPTION_KEY": _FERNET_STUB,
    "JWT_SECRET_KEY": "stub-jwt-secret-32-bytes-minimum-!",
    "ALLOWED_ORIGINS": "http://localhost:8000",
    "LOG_DIR": "",
}
for k, v in _STUBS.items():
    os.environ.setdefault(k, v)

# ── Guard: exit immediately unless RUN_LLM_LIVE=1 ─────────────────────────────
if os.environ.get("RUN_LLM_LIVE") != "1":
    print(
        "llm_e2e.py: RUN_LLM_LIVE is not '1' — skipping live API calls.\n"
        "Set RUN_LLM_LIVE=1 and ANTHROPIC_API_KEY to run against the real API."
    )
    sys.exit(0)

if not os.environ.get("ANTHROPIC_API_KEY"):
    print("llm_e2e.py: ANTHROPIC_API_KEY is not set. Cannot run live tests.", file=sys.stderr)
    sys.exit(1)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("llm_e2e")

# Capture log output to assert no secrets appear in logs
_log_capture = io.StringIO()
_log_handler = logging.StreamHandler(_log_capture)
_log_handler.setLevel(logging.DEBUG)
logging.getLogger().addHandler(_log_handler)

# ── Synthetic fixtures ────────────────────────────────────────────────────────
_FAKE_CREATOR_ID = uuid.uuid4()
_FAKE_TASK_ID = str(uuid.uuid4())
_FAKE_CHANNEL = "TestChannel_e2e"

_FAKE_DNA_BRIEF = (
    "This creator makes short-form cooking tutorial videos for beginners. "
    "Top-performing clips: quick recipe reveal moments, '3-ingredient' hooks. "
    "Audience: 18-35 home cooks who want fast results. Tone: warm, encouraging. "
    "Typical video length: 8-12 minutes. Shorts work well under 45s."
)

_FAKE_SEGMENTS: list[dict] = [
    {"start": 0.0, "end": 3.0, "text": "Hey everyone, welcome back to the channel."},
    {"start": 3.0, "end": 6.0, "text": "Today I'm going to show you the easiest pasta recipe."},
    {"start": 6.0, "end": 10.0, "text": "You only need three ingredients."},
    {"start": 10.0, "end": 15.0, "text": "Salt, olive oil, and pasta — that's it."},
    {"start": 15.0, "end": 20.0, "text": "Let's get started with the water first."},
    {
        "start": 20.0,
        "end": 25.0,
        "text": "Always add a handful of salt. It makes a huge difference.",
    },
    {
        "start": 25.0,
        "end": 30.0,
        "text": "While we wait, let me tell you why most people overcook pasta.",
    },
]
_FAKE_SEGMENTS_JSONB = {"segments": _FAKE_SEGMENTS}
_FAKE_TRANSCRIPT = " ".join(s["text"] for s in _FAKE_SEGMENTS)

_FAKE_VIDEO_TITLE = "Easiest 3-Ingredient Pasta You've Never Tried"

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

# ── Test helpers ──────────────────────────────────────────────────────────────
_failures: list[str] = []
_passes: list[str] = []


def _assert(condition: bool, name: str, detail: str = "") -> None:
    if condition:
        logger.info("[PASS] %s", name)
        _passes.append(name)
    else:
        msg = f"[FAIL] {name}" + (f": {detail}" if detail else "")
        logger.error(msg)
        _failures.append(msg)


def _assert_honesty(text: str, name: str) -> None:
    lower = text.lower()
    found = any(w in lower for w in _HONESTY_WORDS)
    _assert(found, f"{name}: honesty disclaimer present", f"text[:200]={text[:200]!r}")


def _assert_no_pii_in_logs(api_key: str, name: str) -> None:
    captured = _log_capture.getvalue()
    _assert(api_key not in captured, f"{name}: API key absent from logs")


def _assert_usage_nonempty(usage: dict, name: str) -> None:
    _assert(
        bool(usage) and usage.get("input_tokens", 0) > 0,
        f"{name}: usage dict non-empty with input_tokens > 0",
        f"usage={usage}",
    )


# ── Module tests ──────────────────────────────────────────────────────────────


def test_titles() -> None:
    """knowledge/titles.py — title suggestion with web_search."""
    from knowledge.titles import generate_title_suggestions, parse_candidates

    # First call — cache write
    raw1, usage1 = asyncio.run(
        generate_title_suggestions(
            channel_title=_FAKE_CHANNEL,
            dna_brief=_FAKE_DNA_BRIEF,
            stated_identity=None,
            video_title=_FAKE_VIDEO_TITLE,
            transcript_summary=_FAKE_TRANSCRIPT[:500],
            task_id=_FAKE_TASK_ID,
        )
    )
    candidates1 = parse_candidates(raw1)
    _assert(len(candidates1) > 0, "titles: candidates non-empty")
    _assert(
        all("title" in c and c["title"] for c in candidates1),
        "titles: all candidates have title field",
    )
    _assert_honesty("\n".join(c.get("rationale", "") for c in candidates1), "titles")
    _assert_usage_nonempty(usage1, "titles call 1")

    # Second call — should see cache_read > 0 (1h TTL DNA prefix)
    raw2, usage2 = asyncio.run(
        generate_title_suggestions(
            channel_title=_FAKE_CHANNEL,
            dna_brief=_FAKE_DNA_BRIEF,
            stated_identity=None,
            video_title=_FAKE_VIDEO_TITLE,
            transcript_summary=_FAKE_TRANSCRIPT[:500],
            task_id=_FAKE_TASK_ID,
        )
    )
    _assert(
        usage2.get("cache_read", 0) > 0,
        "titles: cache_read_input_tokens > 0 on 2nd same-creator call",
        f"usage2={usage2}",
    )


def test_hooks() -> None:
    """knowledge/hooks.py — hook analysis."""
    from knowledge.hooks import analyze_hook

    raw, usage = asyncio.run(
        analyze_hook(
            channel_title=_FAKE_CHANNEL,
            dna_brief=_FAKE_DNA_BRIEF,
            retention_drop_at_s=5.0,
            retention_at_drop=0.72,
            creator_median_at_drop=0.85,
            transcript_excerpt=_FAKE_TRANSCRIPT,
            task_id=_FAKE_TASK_ID,
        )
    )
    _assert(bool(raw), "hooks: non-empty response")
    _assert_usage_nonempty(usage, "hooks")
    # honesty disclaimer is in the JSON schema field
    _assert_honesty(raw, "hooks")


def test_thumbnails() -> None:
    """knowledge/thumbnails.py — concept generation."""
    from knowledge.thumbnails import generate_thumbnail_concepts, parse_concepts

    fake_patterns = {
        "face_present": "often",
        "dominant_emotions": ["excited", "happy"],
        "text_overlay_style": "bold_caps",
        "typical_colors": "bright reds and yellows",
        "composition_pattern": "close-up face with food in foreground",
        "channel_thumbnail_signature": "Energetic face + vivid food colour block",
    }
    raw, usage = asyncio.run(
        generate_thumbnail_concepts(
            channel_title=_FAKE_CHANNEL,
            dna_brief=_FAKE_DNA_BRIEF,
            patterns=fake_patterns,
            transcript_hook=_FAKE_TRANSCRIPT[:200],
            stated_identity=None,
            task_id=_FAKE_TASK_ID,
        )
    )
    concepts = parse_concepts(raw)
    _assert(len(concepts) > 0, "thumbnails: concepts non-empty")
    _assert_usage_nonempty(usage, "thumbnails")
    _assert_honesty("\n".join(c.get("predicted_ctr_rationale", "") for c in concepts), "thumbnails")


def test_analysis() -> None:
    """analysis/brief.py — per-video analysis."""
    from analysis.brief import generate_video_analysis

    text, usage = asyncio.run(
        generate_video_analysis(
            channel_title=_FAKE_CHANNEL,
            youtube_video_id="dQw4w9WgXcQ",  # fake, no DB lookup
            video_title=_FAKE_VIDEO_TITLE,
            query="Why did this video underperform?",
            video_metrics={"views": 1200, "engagement_rate": 0.032},
            retention_summary={"avg_view_percent": 0.45},
            channel_avg={"avg_views": 5000, "avg_engagement_rate": 0.05},
            dna_brief=_FAKE_DNA_BRIEF,
        )
    )
    _assert(bool(text) and len(text) > 50, "analysis: non-empty response")
    _assert_usage_nonempty(usage, "analysis")
    _assert_honesty(text, "analysis")


def test_dna_brief() -> None:
    """dna/brief.py — DNA brief generation."""
    from dna.brief import generate_brief

    fake_patterns = {
        "top_videos": [{"title": _FAKE_VIDEO_TITLE, "views": 10000}],
        "avg_views": 5000,
        "avg_engagement_rate": 0.05,
        "channel_title": _FAKE_CHANNEL,
    }
    text, usage = asyncio.run(
        generate_brief(
            patterns=fake_patterns,
            channel_title=_FAKE_CHANNEL,
            stated_identity=None,
            task_id=None,  # non-streaming path
        )
    )
    _assert(bool(text) and len(text) > 50, "dna_brief: non-empty response")
    _assert_usage_nonempty(usage, "dna_brief")
    _assert_honesty(text, "dna_brief")


def test_improvement() -> None:
    """improvement/brief.py — improvement brief."""
    from improvement.brief import generate_improvement_brief

    analytics = {
        "avg_views": 5000,
        "avg_engagement_rate": 0.05,
        "top_topics": ["pasta", "quick recipes"],
        "underperformers": ["sourdough"],
    }
    text, usage = asyncio.run(
        generate_improvement_brief(
            channel_title=_FAKE_CHANNEL,
            analytics=analytics,
            dna_brief=_FAKE_DNA_BRIEF,
            task_id=None,  # non-streaming path
        )
    )
    _assert(bool(text) and len(text) > 50, "improvement: non-empty response")
    _assert_usage_nonempty(usage, "improvement")
    _assert_honesty(text, "improvement")


def test_typed_exception_propagation() -> None:
    """Deliberately crafts a bad request; asserts typed SDK exception raised."""
    import anthropic

    from config import settings

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    raised_typed = False
    try:
        client.messages.create(
            model="claude-nonexistent-model-xyz",
            max_tokens=10,
            messages=[{"role": "user", "content": "ping"}],
        )
    except (
        anthropic.BadRequestError,
        anthropic.NotFoundError,
        anthropic.APIStatusError,
    ):
        raised_typed = True
    except Exception as exc:
        _assert(
            False,
            "typed_exception: raised typed error on bad model",
            f"raised bare Exception: {type(exc).__name__}: {exc}",
        )
        return

    _assert(raised_typed, "typed_exception: SDK raises typed error on bad request")


def test_no_pii_in_logs() -> None:
    """Assert the API key never appears in captured log output."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    _assert_no_pii_in_logs(api_key, "global_log_scan")


# ── Main ─────────────────────────────────────────────────────────────────────

TESTS = [
    ("titles", test_titles),
    ("hooks", test_hooks),
    ("thumbnails", test_thumbnails),
    ("analysis", test_analysis),
    ("dna_brief", test_dna_brief),
    ("improvement", test_improvement),
    ("typed_exception", test_typed_exception_propagation),
    ("no_pii_in_logs", test_no_pii_in_logs),
]


def main() -> int:
    logger.info("llm_e2e: starting live API harness (RUN_LLM_LIVE=1)")
    for name, fn in TESTS:
        logger.info("--- test: %s ---", name)
        try:
            fn()
        except Exception as exc:
            msg = f"[FAIL] {name}: unexpected exception: {type(exc).__name__}: {exc}"
            logger.exception(msg)
            _failures.append(msg)

    print(f"\nResults: {len(_passes)} passed, {len(_failures)} failed")
    for f in _failures:
        print(f)
    return 0 if not _failures else 1


if __name__ == "__main__":
    sys.exit(main())
