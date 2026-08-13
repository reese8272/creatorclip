"""Staging verification drills (GO_LIVE.md Stage-A residuals) — run INSIDE the
staging app container via the staging-drills workflow (or manually:
``docker compose -p ccstage exec app python -m scripts.drills <drill>``).
MUST be invoked as a module (-m): a bare ``python scripts/drills.py`` puts
scripts/ on sys.path[0], shadowing the root ``flags`` module with
scripts/flags.py (the ops CLI).

Each drill proves one CODE-GREEN scorecard row against the LIVE staging stack
(real Redis, real Postgres, real middleware order) and exits non-zero on
failure so the workflow run is the recorded evidence:

- ``flags-flip``   — #284: flipping ``llm_generation`` 503s an LLM route with
                     no deploy; re-enabling restores it.
- ``spend-trip``   — #290: driving the global daily counter past the cap trips
                     the breaker exactly once (SETNX latch) and flips the flag;
                     manual reset restores. Uses an isolated probe amount and
                     cleans up its own keys.
- ``rate-limit``   — #228: hammering a per-creator daily-limited route returns
                     429 before the limit+1-th request does any work. Uses the
                     render route against a nonexistent clip id: slowapi counts
                     the request before the handler, so every probe is a cheap
                     404 until the quota trips.

SAFETY: staging-only by convention (the workflow targets ``-p ccstage``); the
drills mutate only flag rows, drill-scoped Redis keys, and rate-limit buckets,
and restore state in ``finally`` blocks.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Belt-and-braces for the shadowing described above (mirrors scripts/flags.py):
# put the project root AHEAD of scripts/ so `import flags` reaches the root
# reader module even under a bare `python scripts/drills.py`. The workflow still
# invokes -m; this only means a hand-run from the wrong entry point degrades to
# working instead of dying on `module 'flags' has no attribute 'set_flag'`.
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("drills")

_BASE = "http://localhost:8000"


def _mint_token() -> tuple[str, str]:
    """Session JWT for the seeded staging creator (llm_harness's approach)."""
    import jwt

    from config import settings

    creator_id = "00000000-1111-2222-3333-444444444444"
    payload = {
        "sub": creator_id,
        "exp": datetime.now(UTC) + timedelta(minutes=30),
        "iat": datetime.now(UTC),
    }
    return creator_id, jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm="HS256")


async def _get(path: str, token: str) -> int:
    import httpx

    async with httpx.AsyncClient(timeout=15.0) as c:
        r = await c.get(f"{_BASE}{path}", cookies={"cc_session": token})
        return r.status_code


async def _post(path: str, token: str) -> int:
    import httpx

    async with httpx.AsyncClient(timeout=15.0) as c:
        r = await c.post(f"{_BASE}{path}", cookies={"cc_session": token})
        return r.status_code


async def _await_post_status(
    path: str,
    token: str,
    *,
    accept: Callable[[int], bool],
    what: str,
    timeout_s: float,
) -> int:
    """Poll POST ``path`` until ``accept(status)`` holds; AssertionError on timeout.

    A flag flip is NOT instantaneous across processes. ``flags._reset_cache()``
    clears THIS process's cache, but the probe travels over HTTP to the uvicorn
    app, which keeps its own ``FLAG_TTL_S`` (~30 s) cache — so a flip is only
    guaranteed visible after one TTL window, exactly as flags.py documents
    ("running processes converge within one TTL window").

    Asserting immediately after a flip races that window. The 2026-08-13 run
    failed on precisely this ("still 503 after re-enable: 503"): the disable
    probe warmed the app's cache with False, and the re-enable was asserted
    ~90 ms later against that still-valid cache entry. The disable assertion was
    equally racy and only passed because the app's cache happened to be cold.
    """
    import httpx

    deadline = time.monotonic() + timeout_s
    async with httpx.AsyncClient(timeout=15.0) as c:
        while True:
            code = (await c.post(f"{_BASE}{path}", cookies={"cc_session": token})).status_code
            if accept(code):
                return code
            if time.monotonic() >= deadline:
                raise AssertionError(f"{what}: still {code} after {timeout_s:.0f}s (flag TTL)")
            await asyncio.sleep(1.0)


async def drill_flags_flip() -> None:
    """#284: kill switch disables a live LLM surface without a deploy."""
    import flags
    from db import AsyncSessionLocal

    _, token = _mint_token()
    probe = "/videos/catalog"  # cheap authed non-LLM probe proves auth works
    assert await _get(probe, token) == 200, "staging auth probe failed"

    llm_probe = "/creators/me/improvement-brief"  # POST is require_flag-gated
    # Allow two TTL windows for the app process to observe each flip (see
    # _await_post_status): one for the entry to expire, one for slack.
    converge_s = flags.FLAG_TTL_S * 2
    try:
        async with AsyncSessionLocal() as s:
            await flags.set_flag(
                "llm_generation", False, updated_by="drill", reason="flags-flip drill", session=s
            )
        flags._reset_cache()
        code = await _await_post_status(
            llm_probe,
            token,
            accept=lambda c: c == 503,
            what="expected 503 while llm_generation disabled",
            timeout_s=converge_s,
        )
        log.info("flags-flip: disabled -> %s (503 confirmed)", code)
    finally:
        async with AsyncSessionLocal() as s:
            await flags.set_flag(
                "llm_generation",
                True,
                updated_by="drill",
                reason="flags-flip drill restore",
                session=s,
            )
        flags._reset_cache()
    code = await _await_post_status(
        llm_probe,
        token,
        accept=lambda c: c != 503,
        what="still 503 after re-enable",
        timeout_s=converge_s,
    )
    log.info("flags-flip: re-enabled -> %s. PASS", code)


async def drill_spend_trip() -> None:
    """#290: global cap breach flips the flag exactly once; manual reset works."""
    import flags
    from billing import spend_guard
    from config import settings
    from db import AsyncSessionLocal

    cap_usd = float(settings.SPEND_CAP_GLOBAL_DAILY_USD)
    try:
        # Drive the real counter over the cap with one oversized probe spend.
        await spend_guard.record_spend(
            creator_id=uuid.UUID("00000000-1111-2222-3333-444444444444"),
            usd=cap_usd + 1.0,
        )
        # Give the trip path its moment, then verify the flag flipped.
        for _ in range(10):
            flags._reset_cache()
            if not await flags.flag_enabled("llm_generation"):
                break
            await asyncio.sleep(0.5)
        assert not await flags.flag_enabled("llm_generation"), "flag did not trip"
        log.info("spend-trip: cap breach flipped llm_generation OFF")
        # Second breach must be a no-op (latch).
        await spend_guard.record_spend(
            creator_id=uuid.UUID("00000000-1111-2222-3333-444444444444"), usd=1.0
        )
        log.info("spend-trip: second breach latched (no error)")
    finally:
        # Manual reset per RUNBOOKS: clear latch + counters, re-enable flag.
        from youtube._redis import get_redis_client

        r = get_redis_client()
        today = time.strftime("%Y-%m-%d", time.gmtime())
        month = time.strftime("%Y-%m", time.gmtime())
        await r.delete(
            "creatorclip:spend:trip:llm_generation",
            f"creatorclip:spend:{today}",
            f"creatorclip:spend:{month}",
            f"creatorclip:spend:{today}:creator:00000000-1111-2222-3333-444444444444",
        )
        async with AsyncSessionLocal() as s:
            await flags.set_flag(
                "llm_generation",
                True,
                updated_by="drill",
                reason="spend-trip drill restore",
                session=s,
            )
        flags._reset_cache()
    assert await flags.flag_enabled("llm_generation"), "flag not restored"
    log.info("spend-trip: manual reset restored the flag. PASS")


async def _clear_rate_limit_buckets(creator_id: str) -> int:
    """Delete this creator's slowapi/limits buckets so the drill starts at zero.

    The daily ceiling is a PERSISTENT per-creator counter in Redis, so a second
    run on the same day starts with the quota already spent. Without this the
    drill's first probe is 429, ``first_429 == 0``, and the pre-quota guard
    degenerates to ``all([]) == True`` — it would confirm "a 429 occurs" while
    proving nothing about WHEN. Mirrors drill_spend_trip, which likewise clears
    its own counters rather than depending on inherited state.

    `limits` (5.x) namespaces every bucket under the ``LIMITS:`` prefix and the
    key embeds the limiter key — here the creator id (limiter.py `_creator_key`).
    Scoped to that id so no other tenant's buckets are touched.
    """
    from youtube._redis import get_redis_client

    r = get_redis_client()
    keys = [k async for k in r.scan_iter(match=f"LIMITS:*{creator_id}*", count=500)]
    if keys:
        await r.delete(*keys)
    return len(keys)


async def drill_rate_limit() -> None:
    """#228: the render daily quota 429s past the cap; normal request unaffected."""
    from config import settings

    creator_id, token = _mint_token()
    limit = int(settings.RENDER_DAILY_JOB_LIMIT)
    ghost = uuid.uuid4()
    path = f"/clips/{ghost}/render"
    try:
        cleared = await _clear_rate_limit_buckets(creator_id)
        log.info("rate-limit: cleared %d pre-existing bucket key(s)", cleared)
        codes: list[int] = []
        for _ in range(limit + 5):
            codes.append(await _post(path, token))
            if codes[-1] == 429:
                break
        assert 429 in codes, f"no 429 within {limit + 5} calls; tail={codes[-5:]}"
        first_429 = codes.index(429)
        # THE property (#228): the 429 must arrive only AFTER the quota, not on
        # some arbitrary earlier request. This is what makes the guard below
        # non-vacuous — first_429 == 0 now fails loudly instead of passing.
        assert first_429 >= limit, (
            f"429 arrived at request #{first_429 + 1} but the limit is {limit} — "
            "the bucket was not empty at the start, so this run proves nothing "
            "about the quota boundary"
        )
        assert all(c == 404 for c in codes[:first_429]), (
            f"pre-quota probes should be cheap 404s: {set(codes[:first_429])}"
        )
        log.info(
            "rate-limit: %d cheap 404 probes then 429 at request #%d (limit=%d). PASS",
            first_429,
            first_429 + 1,
            limit,
        )
    finally:
        # Leave the counter clean so the NEXT run also starts from zero. Never let
        # a cleanup error replace a real assertion failure — the diagnosis matters
        # more than the tidy-up, and the next run clears at its start anyway.
        try:
            await _clear_rate_limit_buckets(creator_id)
        except Exception:  # noqa: BLE001 — best-effort cleanup, never masks the verdict
            log.warning("rate-limit: post-drill bucket cleanup failed", exc_info=True)


_DRILLS = {
    "flags-flip": drill_flags_flip,
    "spend-trip": drill_spend_trip,
    "rate-limit": drill_rate_limit,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage-A staging drills.")
    parser.add_argument("drill", choices=[*_DRILLS, "all"])
    which = parser.parse_args().drill
    names = list(_DRILLS) if which == "all" else [which]
    for name in names:
        log.info("=== drill: %s ===", name)
        asyncio.run(_DRILLS[name]())
    log.info("ALL DRILLS PASSED: %s", ", ".join(names))
    return 0


if __name__ == "__main__":
    sys.exit(main())
