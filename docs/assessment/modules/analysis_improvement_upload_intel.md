# analysis + improvement + upload_intel — assessed 2026-07-20

Anthropic-SDK claims re-verified against the current /claude-api skill reference (2026-07-20):
- **pause_turn** — server-tool turns can return `stop_reason:"pause_turn"`; caller must re-send the
  assistant content with the same `tools` and re-call, capping continuations (~5). Confirmed still the
  required pattern.
- **web_search `max_uses`** — supported optional field on `web_search_20260209`; dynamic filtering
  activates automatically on Sonnet 4.6 (no beta header, no `allowed_callers` requirement documented).
- **Prompt-caching floors (current reference)** — Sonnet 4.5/4.1/4/3.7: 1,024 tok; **Sonnet 4.6 /
  Fable 5: 2,048 tok**; Opus 4.5–4.8 / Haiku 4.5: 4,096 tok. Below-floor markers are silent no-ops
  (no charge, `cache_creation_input_tokens: 0`).

## Resolved since 2026-07-01
- **[SEV1 → FIXED] improvement/brief.py web_search `pause_turn` never handled** — Issue 350 added the
  loop to BOTH paths: `.create()` (:209-221) and streaming via `stream_message` (:153-173), each capped
  at `_MAX_SEARCH_ROUNDS = 5`, appending `{"role":"assistant","content":msg.content}` per round exactly
  as the docs prescribe. Streaming switched from `stream_and_emit` to `stream_message` (full `Message`
  with visible `stop_reason`). Continuation test exists
  (`tests/test_brief_caching.py::test_improvement_brief_pause_turn_loop_continues_on_web_search`).
  DECISIONS entry 2026-07-01 records the change.
- **[SEV2 → FIXED] no `max_uses` on web_search + `_20260209` config concerns** — `max_uses: 5` now on
  the tool definition (improvement/brief.py:98); model is `claude-sonnet-4-6` (config.py:120), which the
  current reference lists as fully supporting `web_search_20260209` dynamic filtering with no
  `allowed_callers` requirement. The prior ZDR/`allowed_callers` concern is moot per current docs.
- **[SEV2 → FIXED] `best_upload_windows` filtered malformed rows AFTER the top-N slice** — Issue 352
  Batch J: upload_intel/timing.py:54-55 now filters+coerces via `_coerce_row` BEFORE
  `sorted(...)[:top_n]`. Regression test present
  (`tests/test_upload_intel.py::test_best_windows_malformed_row_does_not_underfill_top_n`).
- **[SEV2 → FIXED] `optimal_gap_hours` ignored week wraparound** — Issue 352 Batch J:
  upload_intel/timing.py:94-97 uses the shorter arc of the 168-hour circular week
  (`min(diff, 168 - diff)`), fixing the Sat 23:00 → Sun 01:00 = 166h case. Test present
  (`test_optimal_gap_hours_week_wraparound`).
- **[prior note, still good] analysis/brief.py** has no `cache_control` marker (removed Issue 315);
  tokens logged on both paths; singleton client; no web_search so pause_turn n/a.

## Findings

### improvement (`improvement/brief.py`)
- [SEV2] improvement/brief.py:209-248 — the `.create()` pause_turn loop does NOT accumulate `usage`
  across rounds: `response` is overwritten each iteration and `_usage`/`record_llm_metric`/the returned
  usage dict (which callers pass to `billing.ledger.record_llm_usage`) are built from the FINAL round
  only, dropping earlier rounds' output tokens (search preamble + tool_use). The streaming path
  correctly accumulates per-round (`usage[k] += round_usage.get(k, 0)`, :163-164) — the two paths
  disagree, and multi-round searches under-bill on the `.create()` path. | fix: mirror the streaming
  path — initialize a zeroed usage dict before the loop and add each round's
  `response.usage.{input,output,cache_*}` inside it; add a two-round pause_turn test asserting summed
  usage.
- [cleanup, carry-forward] improvement/brief.py:84-93 — `cache_control:{"type":"ephemeral"}` on the
  ~400-token static prefix is still an inert no-op (below both the 1,024 and the current 2,048 Sonnet 4.6
  floor). Now explicitly documented in the module docstring (:5-7) with a DECISIONS pointer, and per
  current docs a below-floor marker carries no charge — downgraded from the prior SEV2. Still dead code
  inconsistent with sibling `analysis/brief.py` (marker removed, Issue 315). | fix: drop the marker to
  match Issue 315, or consolidate a shared static prefix that clears the floor so caching actually
  engages.
- [cleanup] improvement/brief.py:6,63 and analysis/brief.py:5,8,85,111-112 — comments cite "Sonnet
  4.6's 1024-token floor"; the current reference lists Sonnet 4.6 at **2,048** tokens (1,024 is the
  Sonnet 4.5 floor). No behavioral impact (~400-660 tok is below either), but the Issue-315 DECISIONS
  claim "1024 supersedes ALL 2048 refs" conflicts with today's docs. | fix: update the comments to
  "below the cacheable floor (1–2K tokens depending on model)" or re-verify and correct the number;
  amend the DECISIONS entry.
- [cleanup, carry-forward] improvement/brief.py:71 — `_build_request(...) -> tuple` bare return | fix:
  `-> tuple[list[dict], list[dict], list[dict]]`. Same in analysis/brief.py:78:
  `-> tuple[list[dict], list[dict]]`.
- Note (no finding): on max-rounds exhaustion both paths return the last pause_turn message's final
  text block (possibly the search preamble) — but a warning is logged (:170-173, :221) and `max_uses: 5`
  makes 6 consecutive pause_turns effectively unreachable. Acceptable bounded degenerate case.

### analysis (`analysis/brief.py`)
- [cleanup] analysis/brief.py:46-47 — comment "Static instruction block — carries the cache breakpoint
  (same pattern as improvement/brief.py and dna/brief.py)" is stale: this file has NO `cache_control`
  marker (removed Issue 315, as the docstring itself says). | fix: reword to "Static instruction block
  (no cache marker — below the cacheable floor, Issue 315)".
- Otherwise clean: singleton `AsyncAnthropic` (:34), tokens logged + `record_llm_metric` on both paths
  (:179-186, :211-227), `warn_if_truncated` on `.create` (:228) and inside `stream_and_emit`
  (worker/anthropic_stream.py:106), no tools → no pause_turn exposure, disclaimer appended in Python,
  no PII/token in logs, error paths log exception type only.

### upload_intel (`upload_intel/timing.py`)
- No findings. Pure deterministic, no LLM, rows pre-scoped per creator by callers, bounded ≤168
  rows/creator, shared `_coerce_row` validation, both Issue 352 Batch J fixes verified with tests.

## Rubric coverage
| Category | analysis | improvement | upload_intel |
|---|---|---|---|
| 1 Resource lifecycle | ok (singleton) | ok (singleton) | n/a |
| 2 Concurrency & scale | ok (async, bounded) | ok (rounds capped 5, max_uses 5) | ok (≤168 rows, sync) |
| 3 Security & compliance | ok | ok (no secrets logged; disclaimer) | ok (rows pre-scoped) |
| 4 Clip-quality | n/a (not a clip module) | n/a | n/a |
| 5 Anthropic SDK | 1 (cleanup: stale comment) | 1 SEV2 (usage under-count) + 2 cleanup (inert marker, floor number) | n/a |
| 6 Cleanliness & typing | 1 (cleanup: untyped tuple) | 1 (cleanup: untyped tuple) | ok |
| 7 Error handling / API | n/a (not a router) | n/a | n/a |
| 8 Config & paths | ok | ok (tool version + max_uses in code; env documented) | ok |

Totals: **blockers 0 · sev1 0 · sev2 1 · cleanup 4.**

## Module verdict
NEEDS-WORK — all four prior SEV findings (incl. the SEV1 pause_turn) are fixed and tested; the one
remaining defect is the `.create()`-path usage under-accumulation across pause_turn rounds
(billing correctness, bounded blast radius). analysis and upload_intel are clean.
