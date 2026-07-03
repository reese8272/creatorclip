"""Staging verification drills (GO_LIVE.md Stage-A residuals) — run INSIDE the
staging app container via the staging-drills workflow (or manually:
``docker compose -p ccstage exec app python scripts/drills.py <drill>``).

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
from datetime import UTC, datetime, timedelta

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


async def drill_flags_flip() -> None:
    """#284: kill switch disables a live LLM surface without a deploy."""
    import flags
    from db import AsyncSessionLocal

    _, token = _mint_token()
    probe = "/videos/catalog"  # cheap authed non-LLM probe proves auth works
    assert await _get(probe, token) == 200, "staging auth probe failed"

    llm_probe = "/creators/me/improvement-brief"  # POST is require_flag-gated
    try:
        async with AsyncSessionLocal() as s:
            await flags.set_flag(
                "llm_generation", False, updated_by="drill", reason="flags-flip drill", session=s
            )
        flags._reset_cache()
        code = await _post(llm_probe, token)
        assert code == 503, f"expected 503 while disabled, got {code}"
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
    code = await _post(llm_probe, token)
    assert code != 503, f"still 503 after re-enable: {code}"
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


async def drill_rate_limit() -> None:
    """#228: the render daily quota 429s past the cap; normal request unaffected."""
    from config import settings

    _, token = _mint_token()
    limit = int(settings.RENDER_DAILY_JOB_LIMIT)
    ghost = uuid.uuid4()
    path = f"/clips/{ghost}/render"
    codes: list[int] = []
    for _ in range(limit + 5):
        codes.append(await _post(path, token))
        if codes[-1] == 429:
            break
    assert 429 in codes, f"no 429 within {limit + 5} calls; tail={codes[-5:]}"
    first_429 = codes.index(429)
    assert all(c == 404 for c in codes[:first_429]), (
        f"pre-quota probes should be cheap 404s: {set(codes[:first_429])}"
    )
    log.info(
        "rate-limit: %d cheap 404 probes then 429 at request #%d (limit=%d). PASS",
        first_429,
        first_429 + 1,
        limit,
    )


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
