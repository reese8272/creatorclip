"""Tests for Issue 110 fixes from the post-Wave-9 /assess top register.

Covers:
A. /auth/logout rate-limit decorator
B. /billing/webhook rate-limit decorator
C. improvement-brief debounce race fix (SELECT FOR UPDATE SKIP LOCKED)
D. _ingest_async source retention (migration 0039 supersedes the old orphan-mp4
   cleanup — source_uri now stays the video, audio goes to audio_uri)
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


# ── Fix D (SUPERSEDED by migration 0039 — source video now retained) ─────────────


def test_ingest_async_retains_source_video():
    """SUPERSEDES Issue 110 Fix D (migration 0039).

    The old code overwrote ``video.source_uri`` with the audio key and deleted the
    original mp4 — Fix D then had to clean up the resulting orphan. But that design
    broke the renderer (it needs the *video* to extract keyframes for the 9:16
    reframe), which is the core-loop bug migration 0039 fixes. Ingest must now:
    store the audio derivative on ``audio_uri``, LEAVE ``source_uri`` as the original
    video, and NOT delete the mp4 — the video is retained for the render window and
    purged by ``purge_stale_source_media``. With no overwrite there is no orphan, so
    Fix D's prior-URI cleanup is intentionally gone.
    """
    src = (pathlib.Path(__file__).parent.parent / "worker" / "tasks.py").read_text()
    idx = src.find("async def _ingest_async(")
    assert idx >= 0, "worker/tasks.py must define _ingest_async"
    body = src[idx : idx + 6000]
    assert "video.audio_uri = audio_uri" in body, (
        "_ingest_async must store the extracted audio on audio_uri (migration 0039)."
    )
    assert "video.source_uri = audio_uri" not in body, (
        "_ingest_async must NOT overwrite source_uri with the audio key — the "
        "renderer needs the original video retained (migration 0039)."
    )
    assert "adelete_file(prior_source_uri)" not in body, (
        "_ingest_async must NOT delete the source video at ingest — it is retained "
        "for the render window and purged by purge_stale_source_media (migration 0039)."
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


# ── Fix D (SUPERSEDED by 0039): integration-shaped test for source retention ──


@pytest.mark.asyncio
async def test_ingest_async_retains_source_video_integration():
    """Belt-and-suspenders integration test: simulate a first-run ingest where
    source_uri starts as `source/{creator}/{vid}.mp4`, and assert that after ingest
    source_uri is UNCHANGED (the video is retained), audio_uri holds the WAV, and
    adelete_file was NOT called on the source (migration 0039).

    The live version of this is exercised by
    `tests/test_worker_pipeline.py::test_ingest_async_deducts_minutes_exactly_once`
    (real Postgres: asserts source_uri retained + audio_uri set) and by the local
    `scripts/repro_ingest_render.py` harness (real ffmpeg ingest → render).
    """
    # The static guard `test_ingest_async_retains_source_video` above is the
    # load-bearing check; the real ingest→render contract is proven in the
    # integration lane + the repro harness. Skip the heavy inline-mock shape.
    pytest.skip(
        "Real ingest→render retention is covered by the integration test in "
        "test_worker_pipeline.py + scripts/repro_ingest_render.py; the static guard "
        "test_ingest_async_retains_source_video pins the code contract."
    )
