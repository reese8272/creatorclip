# analysis + improvement + upload_intel — assessed 2026-07-20 (post-fix)

Re-assessment after the two fix waves (diff ca3305c..e92b93a). In this slice only
`improvement/brief.py` changed (plus the new shared helper `worker/anthropic_stream.py:stream_until_final`
it now calls); `analysis/` and `upload_intel/` are byte-identical to this morning's assessment.

## Resolved since 2026-07-20 (morning)
- **[SEV2 → FIXED] improvement/brief.py `.create()` path did not accumulate usage across pause_turn
  rounds** — commit 319d53d: a zeroed `_usage` dict is initialized before the loop (:192-197) and every
  round adds `response.usage.{input,output,cache_read,cache_creation}` inside the loop (:207-212);
  `record_llm_metric` (:237), the token log line (:225-231), and the returned usage dict (:241) all use
  the summed figure. Regression test asserts summed usage across a two-round pause_turn sequence
  (`tests/test_brief_caching.py::test_improvement_brief_pause_turn_loop_continues_on_web_search`,
  :339-346 — `input 10+20=30`, `output 5+30=35`). The `.create()` and streaming paths now agree.
- **[VERIFIED — refactor preserved behavior] streaming path switched from an inline loop to the shared
  `stream_until_final` helper** (improvement/brief.py:144-154 → worker/anthropic_stream.py:201-256).
  Traced, not assumed:
  - **Summed usage preserved**: helper initializes the same 4-key zeroed dict and does
    `usage[k] += round_usage.get(k, 0)` per round (anthropic_stream.py:231-249) — identical to the
    removed inline code; the summed dict flows to `record_llm_metric` (brief.py:171) and is returned
    to callers for `billing.ledger.record_llm_usage` (brief.py:172).
  - **Warn behavior preserved**: `warn_if_truncated` still fires per round inside `stream_message`
    (anthropic_stream.py:184); the round-cap warning still fires via the loop's `for/else` with the
    caller-supplied format `"improvement_brief streaming: hit max search rounds (%d)"`
    (brief.py:153, anthropic_stream.py:254-255) — same message, same trigger condition (all
    `max_rounds + 1` calls paused), and the last paused message is returned exactly as before.
  - Error handling unchanged: API errors propagate out of the helper; the caller's
    `log_llm_error` + re-raise wrapper is intact (brief.py:155-157).

## Resolved earlier (2026-07-01 wave — spot-rechecked, still fixed)
- [SEV1] pause_turn never handled → loop on BOTH paths, capped at 5, continuation test present.
- [SEV2] no `max_uses` on web_search → `max_uses: 5` on the tool definition (improvement/brief.py:98)
  + `test_improvement_brief_tool_max_uses_is_set`.
- [SEV2] `best_upload_windows` filtered malformed rows after the top-N slice → `_coerce_row` before
  `sorted(...)[:top_n]` (upload_intel/timing.py) + regression test.
- [SEV2] `optimal_gap_hours` week wraparound → shorter arc of the 168-hour circular week + test.

## Findings (all carry-forward cleanups; no new findings from the diff)

### improvement (`improvement/brief.py`)
- [cleanup, carry-forward] improvement/brief.py:84-93 — `cache_control:{"type":"ephemeral"}` on the
  ~400-token static prefix is still an inert no-op (below the Sonnet 4.6 cacheable floor). Documented
  in the docstring (:4-7) and carries no charge per current docs, but remains dead code inconsistent
  with sibling `analysis/brief.py` (marker removed, Issue 315). | fix: drop the marker to match
  Issue 315, or consolidate a shared static prefix that clears the floor.
- [cleanup, carry-forward] improvement/brief.py:63 and analysis/brief.py:5,8,85,111-112 — comments
  still cite a "Sonnet 4.6 1024-token floor"; current reference lists Sonnet 4.6 at **2,048** tokens
  (1,024 is the Sonnet 4.5 floor). The improvement docstring (:6-7) was reworded to drop the number,
  but :63 and all analysis/brief.py sites still say 1024. No behavioral impact (~400-660 tok is below
  either). | fix: reword to "below the cacheable floor (1-2K tokens depending on model)" or correct
  the number; amend the Issue-315 DECISIONS claim.
- [cleanup, carry-forward] improvement/brief.py:71 — `_build_request(...) -> tuple` bare return |
  fix: `-> tuple[list[dict], list[dict], list[dict]]`. Same in analysis/brief.py:78:
  `-> tuple[list[dict], list[dict]]`.
- Note (no finding): on max-rounds exhaustion both paths still return the last paused message's final
  text block; a warning is logged on both (:217, anthropic_stream.py:254-255) and `max_uses: 5` makes
  6 consecutive pause_turns effectively unreachable. Acceptable bounded degenerate case.

### analysis (`analysis/brief.py`) — unchanged since morning
- [cleanup, carry-forward] analysis/brief.py:46-47 — comment "Static instruction block — carries the
  cache breakpoint (same pattern as improvement/brief.py and dna/brief.py)" is stale: this file has NO
  `cache_control` marker (removed Issue 315, as its own docstring says). | fix: reword to "Static
  instruction block (no cache marker — below the cacheable floor, Issue 315)".
- Otherwise clean: singleton `AsyncAnthropic`, tokens logged + `record_llm_metric` on both paths,
  `warn_if_truncated` on both paths, no tools → no pause_turn exposure, disclaimer appended in Python,
  no PII/token in logs.

### upload_intel (`upload_intel/timing.py`) — unchanged since morning
- No findings. Pure deterministic, no LLM, rows pre-scoped per creator by callers, bounded ≤168
  rows/creator, shared `_coerce_row` validation, both Issue 352 Batch J fixes verified with tests.

## Rubric coverage
| Category | analysis | improvement | upload_intel |
|---|---|---|---|
| 1 Resource lifecycle | ok (singleton) | ok (singleton) | n/a |
| 2 Concurrency & scale | ok (async, bounded) | ok (rounds capped 5, max_uses 5) | ok (≤168 rows, sync) |
| 3 Security & compliance | ok | ok (no secrets logged; disclaimer) | ok (rows pre-scoped) |
| 4 Clip-quality | n/a (not a clip module) | n/a | n/a |
| 5 Anthropic SDK | 1 cleanup (stale comment) | 2 cleanup (inert marker, floor number); usage summing now correct on BOTH paths | n/a |
| 6 Cleanliness & typing | 1 cleanup (untyped tuple) | 1 cleanup (untyped tuple) | ok |
| 7 Error handling / API | n/a (not a router) | n/a | n/a |
| 8 Config & paths | ok | ok | ok |

Totals: **blockers 0 · sev1 0 · sev2 0 · cleanup 4.**

## Module verdict
clean — the last open defect (`.create()`-path usage under-accumulation) is fixed with a summed-usage
regression test, the `stream_until_final` refactor demonstrably preserved summed usage + both warn
behaviors, and only cosmetic cleanups (inert cache marker, stale floor comments, two bare tuple
returns) remain.
