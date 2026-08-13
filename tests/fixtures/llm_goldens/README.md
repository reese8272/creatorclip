# LLM response goldens (Issue 476)

Recorded REAL Anthropic API response bodies, replayed in the default unit lane so the
production parse/annotate paths are exercised against genuine model output — no live
calls in CI, no hand-mocked response shapes.

## scoring/

| File | What it is |
|---|---|
| `inputs.json` | Checked-in scorer inputs: a realistic 10-minute video, 6 candidates (5 signal + 1 llm origin), DNA brief, transcript segments. Authored by hand — edit only together with a re-record. |
| `happy_path.json` | A completed (`stop_reason=end_turn`) response from the production `score_candidates` call shape. |
| `truncated.json` | The same request re-issued with `max_tokens=200` — a real `stop_reason=max_tokens` body pinning the cold-start degradation path. |

Each golden stores `{recorded_at, model, stop_reason, schema_sha256, body}` where
`body` is `response.model_dump(mode="json")` — replayed via
`anthropic.types.Message.model_validate` in `tests/test_scoring_goldens.py`.

## Schema pin

`schema_sha256` is the sha256 of the canonical JSON (`sort_keys=True`,
`separators=(",", ":")`) of `clip_engine.scoring._OUTPUT_SCHEMA` at record time.
`tests/test_scoring_goldens.py::test_schema_hash_pinned` compares it against the
CURRENT schema — any `_OUTPUT_SCHEMA` change forces a re-record, so a golden can
never green-stamp a contract it was not recorded against. The same applies to a
`ANTHROPIC_MODEL_SCORING` change (the `model` field is pinned too).

## Re-recording

From the repo root, with the app env vars available (the script refuses to run
without the explicit opt-in):

```bash
RUN_LLM_LIVE=1 python scripts/record_scoring_goldens.py
```

The script routes the checked-in `inputs.json` through the REAL
`score_candidates` — the request is built by production code, captured at the
`_ANTHROPIC.messages.create` boundary, and forwarded to the live API.

**Cost:** 2 live calls against `settings.ANTHROPIC_MODEL_SCORING`
(~$0.10–0.25 at Opus 5 rates for this fixture; measured 2026-08-13:
in=2310 + 2099 cache-write, out=1824 + 200 ≈ $0.07).

**Provenance:** first recorded 2026-08-13 against `claude-opus-5`
(anthropic SDK 0.105.2) from the dev workstation using the project API key.
