"""Issue 476 — nightly live-API behavioral eval for the LLM clip scorer.

Runs ONLY under RUN_LLM_LIVE=1 + a real ANTHROPIC_API_KEY (same gate as
tests/test_llm_live.py); executed by .github/workflows/llm-e2e-nightly.yml.
Every call goes through the REAL ``score_candidates`` — the request is built by
production code; a recording proxy at the ``_ANTHROPIC`` boundary only observes
the raw response so shape checks can run on it.

Three assertion classes, code-graded (fixed assertions on ORDERINGS — no LLM
judge; see docs/DECISIONS.md, Issue 476):

  A — setup-vs-aftermath: on a constructed pair over the same story (one window
      captures the setup→payoff, the other opens after the payoff landed), the
      setup window must outscore the aftermath window. Majority-of-3.
  B — strict response shape: every raw response is end_turn (refusal detected
      explicitly), parses as the {"scores": [...]} contract, every item cites a
      registry principle with in-range numerics, and every candidate index is
      answered. Runs on EVERY response in this module + one dedicated batched
      call over the goldens inputs fixture.
  C — dna_score ordering: with a fixed cooking-channel brief, the on-brief
      candidate must get a higher dna_score than the off-brief tangent from the
      same video. Majority-of-3.

Margins are printed as ``SCORING-MARGIN ...`` lines — the nightly workflow
greps them into the '## Scoring behavioral lane' step-summary section (run
pytest with -s so they reach the results file).

Cost: 7 live calls/night against settings.ANTHROPIC_MODEL_SCORING (~$1 at
Opus 5 rates for these small payloads).
"""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from typing import Any

import pytest

_LIVE = os.environ.get("RUN_LLM_LIVE") == "1" and bool(os.environ.get("ANTHROPIC_API_KEY"))

_skip_unless_live = pytest.mark.skipif(
    not _LIVE,
    reason="Set RUN_LLM_LIVE=1 and ANTHROPIC_API_KEY to run live LLM scoring tests.",
)

_GOLDENS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "fixtures", "llm_goldens", "scoring"
)


# ── Recording proxy + class-B validation (runs on EVERY live response) ───────


class _RecordingProxy:
    """Forwards messages.create to the real module client, keeping the raw
    response so shape assertions can inspect stop_reason / content."""

    def __init__(self, real_client: Any) -> None:
        self._real = real_client
        self.responses: list[Any] = []
        self.messages = self

    async def create(self, **kwargs: Any) -> Any:
        response = await self._real.messages.create(**kwargs)
        self.responses.append(response)
        return response


@contextmanager
def _recording_boundary():
    import clip_engine.scoring as scoring

    proxy = _RecordingProxy(scoring._ANTHROPIC)
    original = scoring._ANTHROPIC
    scoring._ANTHROPIC = proxy  # type: ignore[assignment]
    try:
        yield proxy
    finally:
        scoring._ANTHROPIC = original  # type: ignore[assignment]


def _validate_response_shape(response: Any, n_candidates: int) -> None:
    """Class B — strict contract on a raw live response."""
    from clip_engine.scoring import _PRINCIPLES

    assert response.stop_reason != "refusal", (
        "scoring model returned stop_reason=refusal on a benign clip payload — "
        f"stop_details={getattr(response, 'stop_details', None)!r}"
    )
    assert response.stop_reason == "end_turn", (
        f"scoring response not completed: stop_reason={response.stop_reason!r} "
        "(max_tokens here means the 8000-token cap is too tight for this payload)"
    )
    text = next((b.text for b in response.content if b.type == "text"), None)
    assert text, "no text block in scoring response"
    data = json.loads(text)
    assert isinstance(data, dict) and isinstance(data.get("scores"), list), (
        f"response is not the {{'scores': [...]}} contract: {text[:200]!r}"
    )
    seen: set[int] = set()
    for item in data["scores"]:
        assert set(item) == {"index", "dna_score", "score", "principle", "reasoning"}, (
            f"item keys drifted from the schema: {sorted(item)}"
        )
        assert 0.0 <= float(item["dna_score"]) <= 1.0, f"dna_score out of range: {item}"
        assert 0.0 <= float(item["score"]) <= 1.0, f"score out of range: {item}"
        assert item["principle"] in _PRINCIPLES, (
            f"off-registry principle {item['principle']!r} — the schema enum should prevent this"
        )
        assert str(item["reasoning"]).strip(), f"empty reasoning for index {item['index']}"
        seen.add(int(item["index"]))
    assert seen == set(range(n_candidates)), (
        f"per-index coverage incomplete: answered {sorted(seen)}, wanted 0..{n_candidates - 1}"
    )


async def _score_live(
    candidates: list[dict],
    timeline: dict,
    dna_brief: str,
    segments: list[dict],
) -> list[dict]:
    """Run the REAL score_candidates against the live API, validating every raw
    response (class B) on the way through."""
    from clip_engine.scoring import score_candidates

    with _recording_boundary() as proxy:
        result = await score_candidates(
            [dict(c) for c in candidates],
            timeline,
            dna_brief=dna_brief,
            transcript_segments=segments,
        )
    assert len(proxy.responses) == 1, "expected exactly one batched scoring call"
    _validate_response_shape(proxy.responses[0], len(candidates))
    return result


async def _majority_of_3(
    run_once: Callable[[], Awaitable[tuple[bool, float, str]]], label: str
) -> None:
    """Run a live ordering probe 3 times; require >= 2 correct orderings.

    Prints one SCORING-MARGIN line per run for the nightly step summary.
    """
    wins = 0
    margins: list[float] = []
    for i in range(3):
        ok, margin, detail = await run_once()
        wins += int(ok)
        margins.append(margin)
        print(f"SCORING-MARGIN class={label} run={i + 1} ok={ok} margin={margin:+.3f} {detail}")
    assert wins >= 2, (
        f"class {label}: only {wins}/3 runs produced the expected ordering; margins={margins}"
    )


# ── Class A — setup window must outscore the aftermath window ────────────────

_TRICKSHOT_BRIEF = (
    "CHANNEL: FirstTryFreddy — trick-shot challenge videos.\n"
    "AUDIENCE: Watches for the callout-then-attempt arc: a bold claim, the attempt, "
    "and the honest reaction. The channel's own retention data shows viewers swipe away "
    "from post-celebration chatter — clips that open AFTER the payoff has landed are the "
    "channel's worst performers. Native Shorts length: 25-40 seconds."
)

_TRICKSHOT_TIMELINE = {
    "duration_s": 120.0,
    "events": [
        {"type": "laughter", "start_s": 26.0, "end_s": 30.0, "value": 0.9},
        {"type": "retention_spike", "start_s": 25.0, "end_s": 31.0, "value": 1.7},
    ],
}

_TRICKSHOT_SEGMENTS = [
    {"text": "What is up everybody, welcome back to the channel.", "start": 0.5, "end": 5.0},
    {
        "text": "I bet Marcus fifty dollars I can land this bottle flip off the roof first try. "
        "Everyone in the comments said it was impossible.",
        "start": 10.0,
        "end": 17.0,
    },
    {"text": "Here we go. Lining it up. No hesitation, no practice throw.", "start": 17.5, "end": 23.0},
    {"text": "AND IT LANDS! ARE YOU KIDDING ME! FIRST TRY! PAY UP MARCUS!", "start": 24.0, "end": 30.0},
    {"text": "I still cannot believe that just happened, man.", "start": 30.5, "end": 36.0},
    {"text": "So after that we all just kind of stood around for a while.", "start": 40.5, "end": 46.0},
    {
        "text": "Marcus kept replaying the clip on his phone and shaking his head.",
        "start": 46.5,
        "end": 53.0,
    },
    {"text": "Then we went to get burritos and argued about the next challenge.", "start": 53.5, "end": 60.0},
    {"text": "Honestly the burrito place was mid, not going to lie.", "start": 60.5, "end": 68.0},
]

# One story, two windows: SETUP captures claim → attempt → payoff; AFTERMATH
# opens after the payoff already landed (its [BEFORE] context shows the payoff).
_SETUP_CANDIDATE = {"setup_start_s": 10.0, "start_s": 10.0, "peak_s": 27.0, "end_s": 36.0}
_AFTERMATH_CANDIDATE = {"setup_start_s": 40.0, "start_s": 40.0, "peak_s": 52.0, "end_s": 68.0}


@pytest.mark.llm_live
@_skip_unless_live
async def test_setup_outscores_aftermath_majority_of_3() -> None:
    """Class A: the scorer must prefer the setup→payoff window over the
    aftermath window of the same story — 'Clip the setup, not the aftermath'
    is the engine's core editorial rule and this is its only live gate."""

    async def _run() -> tuple[bool, float, str]:
        result = await _score_live(
            [_SETUP_CANDIDATE, _AFTERMATH_CANDIDATE],
            _TRICKSHOT_TIMELINE,
            _TRICKSHOT_BRIEF,
            _TRICKSHOT_SEGMENTS,
        )
        setup, aftermath = result[0], result[1]
        margin = setup["score"] - aftermath["score"]
        detail = f"setup={setup['score']:.3f} aftermath={aftermath['score']:.3f}"
        return setup["score"] > aftermath["score"], margin, detail

    await _majority_of_3(_run, "A-setup-vs-aftermath")


# ── Class B — strict shape on the realistic goldens fixture ──────────────────


@pytest.mark.llm_live
@_skip_unless_live
async def test_shape_contract_on_goldens_fixture() -> None:
    """Class B: one batched call over the same 6-candidate fixture the goldens
    replay — every index answered, registry principle, in-range numerics, no
    refusal (the strict per-response validation also ran inside _score_live for
    every class A/C call this module made)."""
    with open(os.path.join(_GOLDENS_DIR, "inputs.json"), encoding="utf-8") as fh:
        inputs = json.load(fh)

    result = await _score_live(
        inputs["candidates"],
        inputs["timeline"],
        inputs["dna_brief"],
        inputs["transcript_segments"],
    )
    scores = [c["score"] for c in result]
    assert len({round(s, 4) for s in scores}) > 1, (
        f"live model scored every candidate identically: {scores}"
    )
    for i, c in enumerate(result):
        assert c["dna_match"] is not None, f"candidate {i}: dna_match not set on the DNA path"
    print(
        "SCORING-MARGIN class=B-shape ok=True margin=+0.000 "
        f"spread={max(scores) - min(scores):.3f} n={len(result)}"
    )


# ── Class C — dna_score ordering on an on-brief vs off-brief pair ────────────

_COOKING_BRIEF = (
    "CHANNEL: PanAndSimple — 3-ingredient cooking for absolute beginners.\n"
    "AUDIENCE: College students and first-apartment cooks. They come for fast, "
    "cheap recipes with a visible payoff (the taste-test reaction). The channel's "
    "analytics show off-topic tangents — especially money talk and personal "
    "investing opinions — are its worst-retained segments by a wide margin.\n"
    "PROVEN STYLE: ingredient-count reveals in the first sentence; taste-test "
    "reactions as the payoff beat; total Shorts length 20-40 seconds."
)

_COOKING_TIMELINE = {
    "duration_s": 300.0,
    "events": [
        {"type": "retention_spike", "start_s": 55.0, "end_s": 61.0, "value": 1.5},
        {"type": "laughter", "start_s": 58.0, "end_s": 60.0, "value": 0.6},
    ],
}

_COOKING_SEGMENTS = [
    {"text": "Three ingredients. That is all this pasta needs, I promise.", "start": 30.0, "end": 35.0},
    {"text": "Salt the water like the sea, drop the pasta, save a cup of that starchy water.", "start": 35.5, "end": 43.0},
    {"text": "Olive oil, garlic, the pasta water — watch it turn into an actual sauce.", "start": 43.5, "end": 52.0},
    {"text": "Okay, taste test. Oh. OH. That is restaurant pasta from three ingredients.", "start": 53.0, "end": 60.0},
    {"text": "Total cost, about ninety cents a serving.", "start": 60.5, "end": 65.0},
    {"text": "Quick tangent while the next pot boils.", "start": 150.5, "end": 154.0},
    {"text": "Everyone keeps asking what I think about crypto, so here is my honest take on index funds versus coins.", "start": 154.5, "end": 162.0},
    {"text": "I put half my sponsorship money into a boring index fund every single month.", "start": 162.5, "end": 169.0},
    {"text": "Dollar cost averaging is not exciting but neither is being broke at forty.", "start": 169.5, "end": 176.0},
    {"text": "Anyway, financial advice over, back to the stove.", "start": 176.5, "end": 181.0},
]

_ON_BRIEF_CANDIDATE = {"setup_start_s": 30.0, "start_s": 30.0, "peak_s": 56.0, "end_s": 65.0}
_OFF_BRIEF_CANDIDATE = {"setup_start_s": 150.0, "start_s": 150.0, "peak_s": 170.0, "end_s": 181.0}


@pytest.mark.llm_live
@_skip_unless_live
async def test_dna_score_orders_on_brief_above_off_brief_majority_of_3() -> None:
    """Class C: dna_score must reflect THIS creator's brief — the on-brief
    cooking payoff must beat the off-brief investing tangent from the same
    video. Pins the DNA→dna_score coupling the unit lane can only mock (the
    renamed ranking_composite_sort_order fixture cross-references this test)."""

    async def _run() -> tuple[bool, float, str]:
        result = await _score_live(
            [_ON_BRIEF_CANDIDATE, _OFF_BRIEF_CANDIDATE],
            _COOKING_TIMELINE,
            _COOKING_BRIEF,
            _COOKING_SEGMENTS,
        )
        on_brief, off_brief = result[0], result[1]
        margin = (on_brief["dna_match"] or 0.0) - (off_brief["dna_match"] or 0.0)
        detail = f"on_brief={on_brief['dna_match']:.3f} off_brief={off_brief['dna_match']:.3f}"
        return (on_brief["dna_match"] or 0.0) > (off_brief["dna_match"] or 0.0), margin, detail

    await _majority_of_3(_run, "C-dna-ordering")
