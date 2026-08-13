"""Issue 476 — recorded-response goldens for the LLM clip scorer.

The goldens are REAL Anthropic API response bodies recorded by
``scripts/record_scoring_goldens.py`` against the exact production call shape
(model from ``settings.ANTHROPIC_MODEL_SCORING``, the two-block system prompt,
``output_config`` json_schema). Replaying them here exercises the REAL
``score_candidates`` parse/annotate path in the default unit lane — no live
calls, no mocked response *shapes* (the body is deserialized back through
``anthropic.types.Message``, so SDK model drift is caught too).

Two goldens:
  happy_path.json — a normal end_turn response over the checked-in 6-candidate
                    inputs; pins per-index mapping, clamps, registry membership.
  truncated.json  — the same request re-issued with max_tokens≈200 so the API
                    returned a REAL stop_reason="max_tokens" body; pins the
                    per-item cold-start degradation path.

The schema-hash pin: each golden stores sha256 of the canonical JSON of
``_OUTPUT_SCHEMA`` at record time. If the schema changes, the pin test fails,
forcing a re-record — a golden recorded against a stale schema can never
green-stamp the current contract. Re-record command + cost:
``tests/fixtures/llm_goldens/README.md``.
"""

import hashlib
import json
import os
from unittest.mock import AsyncMock, patch

import pytest

from clip_engine.scoring import _OUTPUT_SCHEMA, _PRINCIPLES, score_candidates

_GOLDENS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "fixtures", "llm_goldens", "scoring"
)
_GOLDEN_NAMES = ("happy_path.json", "truncated.json")


def schema_sha256(schema: dict) -> str:
    """Canonical sha256 of a JSON schema (sorted keys, no whitespace).

    Must stay byte-identical to the copy in scripts/record_scoring_goldens.py —
    the pin only works if both sides canonicalize the same way.
    """
    canonical = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _load_golden(name: str) -> dict:
    with open(os.path.join(_GOLDENS_DIR, name), encoding="utf-8") as fh:
        return json.load(fh)


def _load_inputs() -> dict:
    with open(os.path.join(_GOLDENS_DIR, "inputs.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _replay_message(golden: dict):
    """Deserialize the recorded body back into a real SDK Message object."""
    from anthropic.types import Message

    return Message.model_validate(golden["body"])


async def _run_scorer_against(golden: dict) -> list[dict]:
    """Run the REAL score_candidates over the checked-in inputs, with the
    recorded Message returned at the _ANTHROPIC.messages.create boundary."""
    inputs = _load_inputs()
    message = _replay_message(golden)
    with patch("clip_engine.scoring._ANTHROPIC") as mock_client:
        mock_client.messages.create = AsyncMock(return_value=message)
        result = await score_candidates(
            [dict(c) for c in inputs["candidates"]],
            inputs["timeline"],
            dna_brief=inputs["dna_brief"],
            transcript_segments=inputs["transcript_segments"],
        )
    return result


# ── Golden discovery ─────────────────────────────────────────────────────────


def test_goldens_exist() -> None:
    """Both goldens + the inputs file must be checked in. A missing golden means
    the recorded-response lane silently stopped covering the scorer (Issue 476)."""
    missing = [
        n for n in (*_GOLDEN_NAMES, "inputs.json") if not os.path.isfile(os.path.join(_GOLDENS_DIR, n))
    ]
    assert not missing, (
        f"missing golden fixture(s) {missing} in {_GOLDENS_DIR}. "
        "Record with: RUN_LLM_LIVE=1 python scripts/record_scoring_goldens.py "
        "(see tests/fixtures/llm_goldens/README.md)"
    )


@pytest.mark.parametrize("name", _GOLDEN_NAMES)
def test_schema_hash_pinned(name: str) -> None:
    """Each golden's recorded schema hash must equal the CURRENT _OUTPUT_SCHEMA
    hash. A schema edit without a re-record fails here — a golden recorded
    against a stale contract must never keep the lane green."""
    golden = _load_golden(name)
    current = schema_sha256(_OUTPUT_SCHEMA)
    assert golden["schema_sha256"] == current, (
        f"{name}: recorded schema_sha256 {golden['schema_sha256'][:12]}… != current "
        f"{current[:12]}… — _OUTPUT_SCHEMA changed since this golden was recorded. "
        "Re-record the goldens (tests/fixtures/llm_goldens/README.md)."
    )


@pytest.mark.parametrize("name", _GOLDEN_NAMES)
def test_golden_model_matches_configured_scoring_model(name: str) -> None:
    """The golden must have been recorded against the configured scoring model —
    a model swap (e.g. Opus→Sonnet) without a re-record changes response
    behavior the goldens are supposed to represent."""
    from config import settings

    golden = _load_golden(name)
    assert golden["model"] == settings.ANTHROPIC_MODEL_SCORING, (
        f"{name}: recorded against {golden['model']!r} but "
        f"ANTHROPIC_MODEL_SCORING={settings.ANTHROPIC_MODEL_SCORING!r} — re-record."
    )


# ── Happy-path replay ────────────────────────────────────────────────────────


async def test_happy_path_replay_per_index_mapping() -> None:
    """The recorded end_turn body drives the real parse/annotate path: every
    candidate is annotated from ITS index's item, values are clamped to [0,1],
    the cited principle is in the registry, and dna_match/score are set."""
    golden = _load_golden("happy_path.json")
    assert golden["stop_reason"] == "end_turn", (
        "happy_path golden must be a completed response — re-record"
    )

    # Ground truth: the raw items the model actually returned, keyed by index.
    message = _replay_message(golden)
    text = next(b.text for b in message.content if b.type == "text")
    raw_items = {int(item["index"]): item for item in json.loads(text)["scores"]}

    result = await _run_scorer_against(golden)
    inputs = _load_inputs()
    assert len(result) == len(inputs["candidates"])

    for i, c in enumerate(result):
        raw = raw_items.get(i)
        assert raw is not None, f"model response missing index {i} — re-record a complete golden"
        # Per-index mapping: candidate i carries item i's values (post-clamp).
        assert c["score"] == pytest.approx(min(1.0, max(0.0, float(raw["score"]))))
        assert c["dna_match"] == pytest.approx(min(1.0, max(0.0, float(raw["dna_score"]))))
        assert 0.0 <= c["score"] <= 1.0
        assert 0.0 <= c["dna_match"] <= 1.0
        assert c["principle"] in _PRINCIPLES, (
            f"candidate {i} cites off-registry principle {c['principle']!r}"
        )
        assert c["reasoning"], f"candidate {i} has empty reasoning"


async def test_happy_path_replay_scores_are_discriminative() -> None:
    """The recorded real response must not be a degenerate constant — the
    scorer's whole job is discrimination between candidates."""
    golden = _load_golden("happy_path.json")
    result = await _run_scorer_against(golden)
    scores = {round(c["score"], 4) for c in result}
    assert len(scores) > 1, "recorded response scored every candidate identically — re-record"


# ── Truncated replay ─────────────────────────────────────────────────────────


async def test_truncated_replay_degrades_to_cold_start() -> None:
    """A REAL stop_reason='max_tokens' body (truncated mid-JSON) must degrade
    every unmatched candidate to the cold-start path instead of raising or
    half-annotating: dna_match None, principle from the cold-start rules,
    fallback reasoning for signal-origin candidates."""
    golden = _load_golden("truncated.json")
    assert golden["stop_reason"] == "max_tokens", (
        "truncated golden must be a real max_tokens body — re-record"
    )

    result = await _run_scorer_against(golden)
    inputs = _load_inputs()
    assert len(result) == len(inputs["candidates"])

    # The truncated body must actually be unusable (that is what it exists to
    # prove); with no parseable scores, EVERY candidate takes the cold-start path.
    for i, c in enumerate(result):
        assert c["dna_match"] is None, f"candidate {i}: truncated body must not set dna_match"
        assert 0.0 <= c["score"] <= 1.0
        assert c["principle"] in _PRINCIPLES
        if c.get("origin", "signal") != "llm":
            assert c["reasoning"] == "Fallback: signal-only score", (
                f"candidate {i}: signal-origin fallback wording drifted"
            )
