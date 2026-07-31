"""
Tests for Issue 53 — Structural "no virality" scan across all surfaces.

Asserts that no JSON response body, static asset, or OpenAPI schema description
contains a forbidden virality-promise phrase. The named principle
"Audience-fit over generic virality" is whitelisted — it negates the bad promise.
"""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from main import app

# ── Constants ─────────────────────────────────────────────────────────────────

FORBIDDEN = re.compile(
    r"\b(viral(?:ity)?|guaranteed views|promises?|promised)\b",
    re.IGNORECASE,
)

# Exact strings that are legitimate (negation / principle name / JS built-ins).
# Strip these before scanning so a document containing ONLY whitelisted phrases passes.
# "Promise" (capital P) is the JavaScript Promise API — not a virality claim.
WHITELIST = [
    "Audience-fit over generic virality",
    "does not promise virality",
    "never promise virality",
    "it does not promise virality",
    "No virality predictions made here",
    "not promise virality",
    # Developer docstrings that AFFIRM the honesty posture (these negate the bad
    # promise; they leak into OpenAPI route/field descriptions). Negations, not claims.
    "no virality language",
    "never virality language",
    # JavaScript Promise API — not a compliance phrase
    "Promise.all",
    "Promise.race",
    "Promise.allSettled",
    "Promise.any",
    "Promise.resolve",
    "Promise.reject",
    "new Promise(",
    "await Promise",
]


def _scrub(text: str) -> str:
    """Remove all whitelisted phrases from text before scanning."""
    for phrase in WHITELIST:
        text = text.replace(phrase, "")
    return text


def _assert_clean(content: str, source: str) -> None:
    """Raise AssertionError with context if a forbidden phrase is found."""
    cleaned = _scrub(content)
    match = FORBIDDEN.search(cleaned)
    if match:
        # Provide 80-char context window around the match for debuggability.
        start = max(0, match.start() - 40)
        end = min(len(cleaned), match.end() + 40)
        context = cleaned[start:end].replace("\n", " ")
        raise AssertionError(
            f"Forbidden phrase {match.group()!r} found in {source!r}.\nContext: ...{context}..."
        )


# ── Test 1: OpenAPI response bodies ──────────────────────────────────────────


def test_no_virality_in_openapi_response_bodies() -> None:
    """
    Hit every no-parameter GET route and assert no response body contains a
    forbidden virality-promise phrase.
    """
    schema = app.openapi()

    violations: list[str] = []
    # Context-managed on purpose: a bare TestClient runs EVERY request on its
    # own ephemeral event loop, and this crawler hits /health — the ping would
    # add a dead-loop connection to the session-lifespan health-Redis pool and
    # poison later /health probes ("Event loop is closed" flake, OCB row 68).
    # With the lifespan running, all requests share one loop and the health
    # client used is this lifespan's own, closed cleanly on exit.
    with TestClient(app, raise_server_exceptions=False) as client:
        for path, path_item in schema.get("paths", {}).items():
            get_op = path_item.get("get")
            if get_op is None:
                continue

            # Skip routes that require path or body parameters — can't call them
            # without seeding data, and they're not static text surfaces.
            parameters = get_op.get("parameters", [])
            has_path_param = any(p.get("in") == "path" for p in parameters)
            if has_path_param:
                continue

            resp = client.get(path)
            body = resp.text or ""
            cleaned = _scrub(body)
            match = FORBIDDEN.search(cleaned)
            if match:
                start = max(0, match.start() - 40)
                end = min(len(cleaned), match.end() + 40)
                context = cleaned[start:end].replace("\n", " ")
                violations.append(f"Route GET {path}: found {match.group()!r} — ...{context}...")

    assert not violations, "Virality phrases in API response bodies:\n" + "\n".join(violations)


# ── Test 2: Static assets ─────────────────────────────────────────────────────


def test_no_virality_in_static_assets() -> None:
    """
    Recursively walk static/ and assert no .html, .css, or .js file contains a
    forbidden virality-promise phrase.
    """
    static_dir = Path(__file__).parent.parent / "static"
    if not static_dir.is_dir():
        pytest.skip("static/ directory not found — nothing to scan")

    violations: list[str] = []
    for ext in ("*.html", "*.css", "*.js"):
        for path in static_dir.rglob(ext):
            content = path.read_text(encoding="utf-8", errors="replace")
            cleaned = _scrub(content)
            match = FORBIDDEN.search(cleaned)
            if match:
                start = max(0, match.start() - 40)
                end = min(len(cleaned), match.end() + 40)
                context = cleaned[start:end].replace("\n", " ")
                violations.append(
                    f"{path.relative_to(static_dir.parent)}: "
                    f"found {match.group()!r} — ...{context}..."
                )

    assert not violations, "Virality phrases in static assets:\n" + "\n".join(violations)


# ── Test 3: OpenAPI schema descriptions ───────────────────────────────────────


def _collect_strings(obj: object, key_filter: set[str]) -> list[str]:
    """
    Recursively collect string values from a JSON-like structure where the
    key is in key_filter.
    """
    results: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in key_filter and isinstance(v, str):
                results.append(v)
            else:
                results.extend(_collect_strings(v, key_filter))
    elif isinstance(obj, list):
        for item in obj:
            results.extend(_collect_strings(item, key_filter))
    return results


def test_no_virality_in_openapi_schema_descriptions() -> None:
    """
    Walk all Pydantic field descriptions, route summaries, and route descriptions
    in the OpenAPI schema and assert none contain a forbidden virality phrase.
    """
    schema = app.openapi()

    # Collect from component schemas (field descriptions)
    components = schema.get("components", {}).get("schemas", {})
    component_strings = _collect_strings(components, {"description", "title"})

    # Collect from paths (route summary / description)
    paths = schema.get("paths", {})
    path_strings = _collect_strings(paths, {"summary", "description"})

    violations: list[str] = []
    for text in component_strings + path_strings:
        cleaned = _scrub(text)
        match = FORBIDDEN.search(cleaned)
        if match:
            start = max(0, match.start() - 40)
            end = min(len(cleaned), match.end() + 40)
            context = cleaned[start:end].replace("\n", " ")
            violations.append(f"Schema string: found {match.group()!r} — ...{context}...")

    assert not violations, "Virality phrases in OpenAPI schema strings:\n" + "\n".join(violations)


# ── Test 4: Notification templates and copy strings (Issue 244) ───────────────


def test_no_virality_in_notification_templates() -> None:
    """
    Walk every .txt and .html file under notify/templates/ and assert none
    contain a forbidden virality-promise phrase.
    """
    templates_dir = Path(__file__).parent.parent / "notify" / "templates"
    if not templates_dir.is_dir():
        pytest.skip("notify/templates/ not found — nothing to scan")

    violations: list[str] = []
    for ext in ("*.txt", "*.html"):
        for path in templates_dir.glob(ext):
            content = path.read_text(encoding="utf-8", errors="replace")
            cleaned = _scrub(content)
            match = FORBIDDEN.search(cleaned)
            if match:
                start = max(0, match.start() - 40)
                end = min(len(cleaned), match.end() + 40)
                context = cleaned[start:end].replace("\n", " ")
                violations.append(
                    f"notify/templates/{path.name}: found {match.group()!r} — ...{context}..."
                )

    assert not violations, "Virality phrases in notification templates:\n" + "\n".join(violations)


def test_no_virality_in_notification_copy_module() -> None:
    """Assert that every string in notify/copy.py COPY dict is honesty-constrained."""
    from notify.copy import COPY

    violations: list[str] = []
    for event_type, strings in COPY.items():
        for key, value in strings.items():
            cleaned = _scrub(value)
            match = FORBIDDEN.search(cleaned)
            if match:
                start = max(0, match.start() - 40)
                end = min(len(cleaned), match.end() + 40)
                context = cleaned[start:end].replace("\n", " ")
                violations.append(
                    f"notify.copy.COPY[{event_type!r}][{key!r}]: "
                    f"found {match.group()!r} — ...{context}..."
                )

    assert not violations, "Virality phrases in notification copy module:\n" + "\n".join(violations)


# ── Test 6: Originality guard advisory copy (Issue 375 / 383 corrections) ────
#
# Issue 383 (2026-07-30) verified that no primary source describes a
# "three-strike ladder" for YouTube's inauthentic-content policy, and that the
# policy is not new (renamed 15 July 2025; 16 July 2026 was a clarification,
# not a launch). Restating either claim in the sameness advisory would be a
# false statement about a third party's enforcement policy — a worse honesty
# breach than any bad clip score. This scans the ACTUAL generated copy, not
# just static source text, so it catches a regression in the message builder
# itself, not only a stray phrase left in a docstring.

_FORBIDDEN_MECHANICS = re.compile(
    r"\b(strike|ladder|90.day|permanent(?:ly)?\s+remov|three.strike)\b", re.IGNORECASE
)
_FORBIDDEN_URGENCY = re.compile(r"\bnew policy\b", re.IGNORECASE)


def test_originality_advisory_copy_has_no_strike_ladder_or_new_policy_language() -> None:
    """Generate a real flagged advisory and scan its message text directly."""
    from knowledge.originality import ClipFingerprint, build_advisory

    cluster_vec = [1.0] * 8
    fps = [
        ClipFingerprint(
            clip_id=f"clip-{i}",
            duration_s=45.0,
            setup_lead_s=5.0,
            source_region="early",
            style_key="white_large|blur|True",
            principle="clip-the-setup",
            embedding=cluster_vec,
        )
        for i in range(4)
    ]
    advisory = build_advisory(fps, similarity_threshold=0.9, min_cluster_size=4)

    assert advisory.flagged is True
    assert advisory.message is not None
    cleaned = _scrub(advisory.message)

    assert not _FORBIDDEN_MECHANICS.search(cleaned), advisory.message
    assert not _FORBIDDEN_URGENCY.search(cleaned), advisory.message
    match = FORBIDDEN.search(cleaned)
    assert not match, f"found {match.group() if match else None!r} in: {advisory.message}"
    # Must not speak for YouTube or certify eligibility (advisory-not-guarantee stance).
    assert "may affect monetization eligibility" in advisory.message
    assert "not a certification" in advisory.message
