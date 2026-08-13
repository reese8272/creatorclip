"""Record real Anthropic response goldens for the clip-scoring lane (Issue 476).

Feeds the checked-in inputs (tests/fixtures/llm_goldens/scoring/inputs.json)
through the REAL ``clip_engine.scoring.score_candidates`` call path — the
request Claude receives is built by production code (model from
``settings.ANTHROPIC_MODEL_SCORING``, the two-block system prompt with the
cache breakpoint, ``output_config`` json_schema) — and dumps the raw response
bodies as goldens for tests/test_scoring_goldens.py to replay.

Two goldens per run:
  happy_path.json — the production call, completed (stop_reason=end_turn).
  truncated.json  — the SAME captured request re-issued with max_tokens=200 so
                    the API returns a real stop_reason=max_tokens body.

Gating: refuses to run unless RUN_LLM_LIVE=1 and a real ANTHROPIC_API_KEY are
set — same contract as the llm_live test lane.

Usage (from the repo root, with the app .env loaded):
    RUN_LLM_LIVE=1 python scripts/record_scoring_goldens.py

Cost: 2 live calls against the configured scoring model (~$0.10-0.25 at
Opus 5 rates for the 6-candidate fixture payload).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("record_scoring_goldens")

_REPO_ROOT = Path(__file__).resolve().parent.parent
_GOLDENS_DIR = _REPO_ROOT / "tests" / "fixtures" / "llm_goldens" / "scoring"


def schema_sha256(schema: dict) -> str:
    """Canonical sha256 of a JSON schema (sorted keys, no whitespace).

    Must stay byte-identical to the copy in tests/test_scoring_goldens.py —
    the schema pin only works if both sides canonicalize the same way.
    """
    canonical = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class _RecordingClient:
    """Proxy standing in for ``clip_engine.scoring._ANTHROPIC``.

    Forwards ``messages.create`` to the real AsyncAnthropic client and captures
    the exact kwargs production code built plus the raw response — so the
    recorded golden is guaranteed to correspond to the production call shape.
    """

    def __init__(self, real_client: Any) -> None:
        self._real = real_client
        self.captured_kwargs: dict | None = None
        self.captured_response: Any = None
        self.messages = self

    async def create(self, **kwargs: Any) -> Any:
        self.captured_kwargs = kwargs
        response = await self._real.messages.create(**kwargs)
        self.captured_response = response
        return response


def _golden(response: Any, model: str, schema_hash: str) -> dict:
    return {
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "model": model,
        "stop_reason": response.stop_reason,
        "schema_sha256": schema_hash,
        "body": response.model_dump(mode="json"),
    }


def _write(name: str, payload: dict) -> None:
    path = _GOLDENS_DIR / name
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    logger.info("wrote %s (stop_reason=%s)", path, payload["stop_reason"])


async def _record() -> int:
    import httpx
    from anthropic import AsyncAnthropic

    import clip_engine.scoring as scoring
    from config import settings

    inputs = json.loads((_GOLDENS_DIR / "inputs.json").read_text(encoding="utf-8"))
    schema_hash = schema_sha256(scoring._OUTPUT_SCHEMA)
    model = settings.ANTHROPIC_MODEL_SCORING

    real_client = AsyncAnthropic(
        api_key=settings.ANTHROPIC_API_KEY,
        timeout=httpx.Timeout(120.0, connect=10.0),
        max_retries=2,
    )
    recorder = _RecordingClient(real_client)

    original = scoring._ANTHROPIC
    scoring._ANTHROPIC = recorder  # type: ignore[assignment]
    try:
        await scoring.score_candidates(
            [dict(c) for c in inputs["candidates"]],
            inputs["timeline"],
            dna_brief=inputs["dna_brief"],
            transcript_segments=inputs["transcript_segments"],
        )
    finally:
        scoring._ANTHROPIC = original  # type: ignore[assignment]

    if recorder.captured_kwargs is None or recorder.captured_response is None:
        logger.error("score_candidates never reached the Anthropic boundary — nothing recorded")
        return 1

    happy = recorder.captured_response
    if happy.stop_reason != "end_turn":
        logger.error(
            "happy-path call ended with stop_reason=%s (wanted end_turn) — not writing goldens; "
            "raise max_tokens in scoring.py or retry",
            happy.stop_reason,
        )
        return 1
    _write("happy_path.json", _golden(happy, model, schema_hash))

    # Truncated golden: the SAME production-built request, capped at ~200
    # output tokens so the API returns a real stop_reason=max_tokens body.
    truncated_kwargs = {**recorder.captured_kwargs, "max_tokens": 200}
    truncated = await real_client.messages.create(**truncated_kwargs)
    if truncated.stop_reason != "max_tokens":
        logger.error(
            "truncated call ended with stop_reason=%s (wanted max_tokens) — not writing golden",
            truncated.stop_reason,
        )
        return 1
    _write("truncated.json", _golden(truncated, model, schema_hash))

    logger.info(
        "done: 2 goldens recorded against %s (happy in=%d out=%d; truncated out=%d)",
        model,
        happy.usage.input_tokens,
        happy.usage.output_tokens,
        truncated.usage.output_tokens,
    )
    return 0


def main() -> int:
    if os.environ.get("RUN_LLM_LIVE") != "1":
        logger.error("refusing to run: set RUN_LLM_LIVE=1 (this script makes real API calls)")
        return 1

    sys.path.insert(0, str(_REPO_ROOT))
    from config import settings

    if not settings.ANTHROPIC_API_KEY:
        logger.error("refusing to run: no ANTHROPIC_API_KEY configured")
        return 1

    return asyncio.run(_record())


if __name__ == "__main__":
    raise SystemExit(main())
