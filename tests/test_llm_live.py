"""Issue 319 — flag-gated live-API LLM verification tests.

Tests marked ``llm_live`` are excluded from the default unit lane (pytest.ini
addopts deselects them). They run ONLY when ``RUN_LLM_LIVE=1`` is set and a real
ANTHROPIC_API_KEY is available — intended for nightly CI via
.github/workflows/llm-e2e-nightly.yml.

The always-running guard test (no mark) verifies that llm_live IS excluded from
the default addopts lane — if someone removes the deselection, this test catches it.
"""

from __future__ import annotations

import os

import pytest

_LIVE = os.environ.get("RUN_LLM_LIVE") == "1" and bool(os.environ.get("ANTHROPIC_API_KEY"))

_skip_unless_live = pytest.mark.skipif(
    not _LIVE,
    reason="Set RUN_LLM_LIVE=1 and ANTHROPIC_API_KEY to run live LLM tests.",
)

# ── Always-running guard (no llm_live mark — MUST run in the default lane) ───


def test_llm_live_mark_excluded_from_default_lane() -> None:
    """Verify the llm_live mark is deselected in the default unit lane.

    This test has NO llm_live mark so it always runs. It verifies that pytest.ini's
    addopts contains 'not llm_live', preventing accidental live API calls in CI.
    If the filter is removed, this test fails and the gap is caught immediately.
    """
    import configparser
    from pathlib import Path

    pytest_ini = Path(__file__).parent.parent / "pytest.ini"
    parser = configparser.ConfigParser()
    parser.read(pytest_ini)
    addopts = parser.get("pytest", "addopts", fallback="")
    assert "llm_live" in addopts, (
        "pytest.ini addopts does not deselect 'llm_live' tests. "
        "Add 'not llm_live' to the -m filter in pytest.ini so that live API tests "
        "never run in the default unit lane."
    )


# ── Live tests (marked llm_live — excluded from default lane) ────────────────


@pytest.mark.llm_live
@_skip_unless_live
def test_titles_live() -> None:
    """knowledge/titles.py returns valid candidates with honesty language."""
    from knowledge.titles import generate_title_suggestions, parse_candidates

    fake_dna = "Cooking channel for beginners. Top hooks: 3-ingredient reveals."
    raw, usage = generate_title_suggestions(
        channel_title="TestChannel",
        dna_brief=fake_dna,
        stated_identity=None,
        video_title="Easy Pasta Recipe",
        transcript_summary="Three ingredients only: salt, olive oil, pasta.",
        task_id="live-test-task",
    )
    candidates = parse_candidates(raw)
    assert candidates, "titles: candidates non-empty"
    assert usage.get("input_tokens", 0) > 0, f"titles: usage={usage}"
    honesty_words = {"estimate", "likely", "suggests", "grounded", "not a guarantee"}
    rationale_text = " ".join(c.get("rationale", "") for c in candidates).lower()
    assert any(w in rationale_text for w in honesty_words), (
        f"titles: no honesty language found. rationale_text[:300]={rationale_text[:300]!r}"
    )


@pytest.mark.llm_live
@_skip_unless_live
def test_hooks_live() -> None:
    """knowledge/hooks.py returns a HookReport with honesty disclaimer."""
    from knowledge.hooks import analyze_hook, parse_hook_report

    fake_dna = "Cooking channel for beginners."
    raw, usage = analyze_hook(
        channel_title="TestChannel",
        dna_brief=fake_dna,
        retention_drop_at_s=5.0,
        retention_at_drop=0.72,
        creator_median_at_drop=0.85,
        transcript_excerpt="Welcome back. Today easiest 3-ingredient pasta.",
        task_id="live-test-task",
    )
    assert usage.get("input_tokens", 0) > 0, f"hooks: usage={usage}"
    report = parse_hook_report(raw)
    assert report.get("diagnosis"), "hooks: diagnosis field non-empty"
    assert report.get("honesty_disclaimer"), "hooks: honesty_disclaimer present"


@pytest.mark.llm_live
@_skip_unless_live
def test_dna_brief_usage_nonempty_live() -> None:
    """dna/brief.py: response is non-empty and usage dict is populated."""
    from dna.brief import generate_brief

    patterns: dict = {"avg_views": 5000, "channel_title": "TestChannel"}
    text, usage = generate_brief(patterns=patterns, channel_title="TestChannel", task_id=None)
    assert text and len(text) > 50, "dna_brief: response too short"
    assert usage.get("input_tokens", 0) > 0, f"dna_brief: usage empty: {usage}"


@pytest.mark.llm_live
@_skip_unless_live
def test_titles_cache_hit_live() -> None:
    """knowledge/titles.py: 2nd same-DNA call shows cache_read > 0 (1h TTL prefix)."""
    from knowledge.titles import generate_title_suggestions

    fake_dna = "Cooking channel. " * 50  # ~100 words — enough to clear the 1024-token floor
    kwargs = dict(
        channel_title="TestChannel",
        dna_brief=fake_dna,
        stated_identity=None,
        video_title="Easy Pasta Recipe",
        transcript_summary="Salt, olive oil, pasta.",
        task_id="live-cache-test",
    )
    _, usage1 = generate_title_suggestions(**kwargs)  # type: ignore[arg-type]
    _, usage2 = generate_title_suggestions(**kwargs)  # type: ignore[arg-type]
    assert usage2.get("cache_read", 0) > 0, (
        f"titles: cache_read_input_tokens not > 0 on 2nd call. usage2={usage2}"
    )


@pytest.mark.llm_live
@_skip_unless_live
def test_typed_exception_on_bad_model_live() -> None:
    """SDK raises a typed exception (not bare Exception) for an invalid model."""
    import anthropic

    from config import settings

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    with pytest.raises(
        (anthropic.BadRequestError, anthropic.NotFoundError, anthropic.APIStatusError)
    ):
        client.messages.create(
            model="claude-nonexistent-model-xyz",
            max_tokens=10,
            messages=[{"role": "user", "content": "ping"}],
        )


@pytest.mark.llm_live
@_skip_unless_live
def test_no_api_key_in_logs_live() -> None:
    """Captured log output does not contain the real API key."""
    import io
    import logging

    capture = io.StringIO()
    handler = logging.StreamHandler(capture)
    logging.getLogger().addHandler(handler)

    try:
        from dna.brief import generate_brief

        patterns: dict = {"avg_views": 1000, "channel_title": "TestChannel"}
        generate_brief(patterns=patterns, channel_title="TestChannel", task_id=None)
    finally:
        logging.getLogger().removeHandler(handler)

    captured = capture.getvalue()
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    assert api_key not in captured, "API key leaked into log output"
