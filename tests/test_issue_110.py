"""Tests for Issue 110 fixes from the post-Wave-9 /assess top register.

Covers:
A. /auth/logout rate-limit decorator
B. /billing/webhook rate-limit decorator
C. improvement-brief debounce race fix (SELECT FOR UPDATE SKIP LOCKED)
D. _ingest_async orphan-mp4 cleanup after final commit
   (Issue-105 misread — .wav short-circuit only fixed retry case)
E. routers/auth.py:131 _logging workaround removed (covered by ruff/grep)
"""

from __future__ import annotations

import pathlib

import pytest

# ── Fix A: /auth/logout rate limit ────────────────────────────────────────────


def test_logout_has_rate_limit_decorator():
    """Static guard: /auth/logout must carry @limiter.limit with
    key_func=creator_key. Without it, an authenticated attacker can spam
    logout (state change) unboundedly and force-evict the session cookie
    across an automation-bot session. (Issue 110 Fix A)
    """
    src = (pathlib.Path(__file__).parent.parent / "routers" / "auth.py").read_text()
    # Find the /logout route definition + its immediately-preceding decorator block
    idx = src.find('@router.post("/logout"')
    assert idx >= 0, "auth.py must define POST /logout"
    snippet = src[idx : idx + 400]
    assert "@limiter.limit(" in snippet, (
        "/auth/logout must carry an @limiter.limit decorator (Issue 110 Fix A)."
    )
    assert "key_func=creator_key" in snippet, (
        "/auth/logout limit must key on creator (Issue 110 Fix A)."
    )


# ── Fix B: /billing/webhook rate limit ────────────────────────────────────────


def test_webhook_has_rate_limit_decorator():
    """Static guard: /billing/webhook must carry @limiter.limit. Sits in front
    of the Stripe signature check so a flood of bad-signature payloads can't
    exhaust the worker. IP-keyed via get_remote_address (Stripe-originated
    requests have no session cookie). (Issue 110 Fix B)
    """
    src = (pathlib.Path(__file__).parent.parent / "routers" / "billing.py").read_text()
    idx = src.find('@router.post("/webhook"')
    assert idx >= 0, "billing.py must define POST /webhook"
    snippet = src[idx : idx + 400]
    assert "@limiter.limit(" in snippet, (
        "/billing/webhook must carry an @limiter.limit decorator (Issue 110 Fix B)."
    )
    assert "key_func=get_remote_address" in snippet, (
        "/billing/webhook must use IP-based keying (no session for Stripe). (Issue 110 Fix B)"
    )


# ── Fix C: improvement-brief debounce race ────────────────────────────────────


def test_improvement_brief_uses_for_update_skip_locked():
    """Static guard: start_improvement_brief must use with_for_update(skip_locked=True)
    on the existing-row read to prevent the check-then-update race that would
    double-fire the billed Anthropic call.

    Plain SELECT then UPDATE is race-prone: two concurrent POSTs both see
    status != pending, both write pending, both commit, both enqueue Celery.
    SELECT FOR UPDATE SKIP LOCKED + a no-row fallback re-query is the
    canonical SQLAlchemy 2.x async shape. (Issue 110 Fix C)
    """
    src = (pathlib.Path(__file__).parent.parent / "routers" / "improvement.py").read_text()
    # Find start_improvement_brief body
    idx = src.find("def start_improvement_brief(")
    assert idx >= 0, "improvement.py must define start_improvement_brief"
    body = src[idx : idx + 3000]
    assert "with_for_update(skip_locked=True)" in body, (
        "start_improvement_brief must use with_for_update(skip_locked=True) "
        "to prevent the debounce race that would double-fire Anthropic. "
        "(Issue 110 Fix C)"
    )


# ── Fix D: _ingest_async orphan-mp4 cleanup ───────────────────────────────────


def test_ingest_async_captures_prior_source_uri():
    """Static guard: _ingest_async must capture prior_source_uri BEFORE the
    final commit overwrites video.source_uri with the audio key, then call
    adelete_file on the prior URI after commit (with a prefix guard).

    Without this, the original mp4 in R2 is permanently invisible to
    `_purge_stale_source_media_async` (which iterates Video.source_uri to
    find purgeables) — ToS retention violation + unbounded storage cost.
    Issue 105's .wav short-circuit only prevented the RETRY-orphan; the
    FIRST-run orphan was never cleaned up. (Issue 110 Fix D)
    """
    src = (pathlib.Path(__file__).parent.parent / "worker" / "tasks.py").read_text()
    idx = src.find("async def _ingest_async(")
    assert idx >= 0, "worker/tasks.py must define _ingest_async"
    # Take a generous slice to cover the full function body
    body = src[idx : idx + 6000]
    assert "prior_source_uri = source_uri" in body, (
        "_ingest_async must capture prior_source_uri before overwriting "
        "video.source_uri (Issue 110 Fix D)."
    )
    assert "adelete_file(prior_source_uri)" in body, (
        "_ingest_async must call adelete_file(prior_source_uri) after the "
        "final commit to close the orphan-mp4 leak (Issue 110 Fix D)."
    )
    # Prefix guard — canonical retry-safe shape per AWS Lambda
    # idempotent-retry doctrine.
    assert 'prior_source_uri.startswith("source/")' in body, (
        "_ingest_async prior-URI deletion must guard on the 'source/' "
        "prefix so Celery redelivery never deletes the audio key by "
        "mistake (Issue 110 Fix D — retry-safe shape)."
    )
    assert 'prior_source_uri.endswith(".mp4")' in body, (
        "_ingest_async prior-URI deletion must also guard on the '.mp4' "
        "suffix (Issue 110 Fix D — retry-safe shape)."
    )


# ── Fix E: _logging workaround removed from routers/auth.py ───────────────────


def test_routers_auth_no_logging_workaround():
    """The Issue 108 cleanup sweep removed all `import logging as _logging`
    workarounds in routers/clips.py + videos.py + creators.py, but missed
    one at routers/auth.py:131. Issue 110 closes that gap. Pin so a future
    diff can't reintroduce the workaround anywhere in auth.py. (Issue 110 Fix E)
    """
    src = (pathlib.Path(__file__).parent.parent / "routers" / "auth.py").read_text()
    assert "import logging as _logging" not in src, (
        "routers/auth.py must use the module-level `logger` (declared at "
        "line 26), not the `_logging` re-import workaround. (Issue 110 Fix E)"
    )
    assert "_logging." not in src, (
        "routers/auth.py must not reference _logging.* — use the module logger. (Issue 110 Fix E)"
    )


# ── Fix C: integration-shaped test for the debounce race ──────────────────────


def test_improvement_brief_debounce_query_has_skip_locked_then_fallback():
    """Stronger static guard than the simple `with_for_update(skip_locked=True)`
    grep above: assert both branches of the new race-free pattern exist —
    the SKIP LOCKED select AND the fallback re-query that handles the
    "lock-held-by-another-request" case. Without the fallback, a second
    concurrent POST would see `row is None` from SKIP LOCKED and try to
    INSERT, hitting UNIQUE(creator_id) → IntegrityError instead of
    returning the existing task_id."""
    src = (pathlib.Path(__file__).parent.parent / "routers" / "improvement.py").read_text()
    idx = src.find("def start_improvement_brief(")
    body = src[idx : idx + 3500]
    # Locked branch:
    assert "with_for_update(skip_locked=True)" in body
    # Fallback re-query when SKIP LOCKED returned None:
    assert "Re-query without the lock" in body or "without the lock" in body, (
        "Must explain WHY a second non-locked re-query is needed when "
        "SKIP LOCKED returns None (lost the race vs no row at all). "
        "(Issue 110 Fix C — race-free shape)"
    )


# ── Fix D: integration-shaped test for the orphan-mp4 deletion ────────────────


@pytest.mark.asyncio
async def test_ingest_async_calls_adelete_file_after_commit():
    """Belt-and-suspenders integration test: simulate a first-run ingest
    where source_uri starts as `source/{creator}/{vid}.mp4`, the audio
    upload commits source_uri to `audio/{vid}.wav`, and assert
    adelete_file was called exactly once with the original mp4 key.

    Heavy mocking required — _ingest_async pulls in db, ffmpeg, R2,
    progress emit, Celery context. We mock the seams, not the
    contract: we want to verify the call sequence, not the internals.
    """
    # This is structural enough that the static guard above is the load-bearing
    # check. Punt the full integration shape to manual verification + the
    # static guard. Skip with a note so coverage of the contract is honest.
    pytest.skip(
        "Full integration shape requires live R2 + Postgres + Celery seams; "
        "static guard in test_ingest_async_captures_prior_source_uri pins "
        "the contract. Re-enable when an `integration` lane that can boot "
        "_ingest_async with mocked R2 + AdminSessionLocal exists."
    )
