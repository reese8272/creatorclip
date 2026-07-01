# analysis + improvement + upload_intel — assessed 2026-07-01

All Anthropic-SDK claims verified against current official docs (not memory):
- **Prompt-caching floor** — https://platform.claude.com/docs/en/build-with-claude/prompt-caching → Sonnet models **1,024 tokens**; Claude 3.5 Haiku 1,024; **Haiku 4.5 4,096**. Below the floor the prompt is "processed without caching, and no error is returned" (`cache_creation`/`cache_read` both 0 — no charge). Checked 2026-07-01.
- **web_search `pause_turn`** — https://platform.claude.com/docs/en/agents-and-tools/tool-use/server-tools + .../web-search-tool → on a long server-tool turn the API returns `stop_reason:"pause_turn"`; the caller **must** re-send the assistant content as-is with the same `tools`, repeating until a different stop_reason, capping continuations. Raw `messages.create`/`messages.stream` do **not** auto-continue. Checked 2026-07-01.

## analysis  (`analysis/brief.py`, `analysis/__init__.py` — empty)
- **[cleanup] analysis/brief.py:77** — `_build_request(...) -> tuple` untyped bare return | fix: `-> tuple[list[dict], list[dict[str, str]]]`.
- **[prior-flag resolved / stale]** Last run flagged "analysis inert prompt-cache markers." Stale: the module has **no** `cache_control` marker (removed Issue 315). Docstring's rationale (Block1 ~410 + Block2 DNA ~250 ≈ 660 tok < 1,024 floor) matches live docs. Tokens logged on both streaming (:178) and `.create` (:210) paths; singleton client (:33); no web_search so pause_turn n/a.
- **Verdict: clean.**

## improvement  (`improvement/brief.py`, `improvement/__init__.py` — empty)
- **[SEV1] improvement/brief.py:181-218 (.create) and :132-168 (streaming)** — web_search `pause_turn` never handled. Tool enabled at :96 but neither path checks `response.stop_reason`; both return `text_blocks[-1].text` (:218 / :168). On a paused turn the last *text* block is the "let me search…" preamble, not the synthesised brief — user silently gets a truncated brief + disclaimer. `warn_if_truncated` only catches `max_tokens` (observability.py:285), so pause_turn is undetected. Made more likely by :96 using dynamic-filtering `web_search_20260209` with **no `max_uses`**. | fix: after `.create()`, `while response.stop_reason == "pause_turn"`: append `{"role":"assistant","content":response.content}` and re-call with the **same** tools, cap ~5 iters. For streaming, switch from `stream_and_emit` (discards non-text blocks) to `stream_message` (worker/anthropic_stream.py:132, returns full Message) and loop the same way. Add a pause_turn continuation test. *(needs-runtime-confirmation on frequency; the missing path is definitive.)*
- **[SEV2] improvement/brief.py:91** — `cache_control:{"type":"ephemeral"}` on the static prefix is **inert**: prefix ≈ 400 tok (`UNTRUSTED_CONTENT_POLICY` 683 chars ≈170 tok + instructions) < verified 1,024 Sonnet floor, so caching never engages. "Carries the cache breakpoint" comments (:39, :84) are misleading, and `analysis/brief.py` already removed this exact marker (Issue 315) — inconsistent siblings. NB per current docs a below-floor marker is a **no-op with no charge**, so this is *not* a cost bug and the Issue-315 "write-premium charge" rationale is inaccurate against today's docs — but it's dead/misleading code that leaves "prompt caching mandatory" unmet. | fix: drop the marker (match Issue 315) or consolidate a shared static prefix that clears 1,024 tokens so caching actually engages; fix comments.
- **[SEV2, needs-runtime-confirmation] improvement/brief.py:96 / config.py:130** — `ANTHROPIC_WEB_SEARCH_TOOL` defaults to `web_search_20260209` (dynamic filtering) with no `allowed_callers`/`max_uses`. Per live server-tools doc, `_20260209`+ run internal code execution, are **not ZDR-eligible by default**, and on models without programmatic tool calling require `allowed_callers:["direct"]` or the request 400s. | fix: confirm the improvement model supports programmatic tool calling; set `allowed_callers:["direct"]` if ZDR wanted; add `max_uses` (~5).
- **[cleanup] improvement/brief.py:66** — `_build_request(...) -> tuple` untyped | fix: `-> tuple[list[dict], list[dict], list[dict]]`.
- **Verdict: NEEDS-WORK** (SEV1 pause_turn).

## upload_intel  (`upload_intel/timing.py`, `upload_intel/__init__.py` — empty)
Pure deterministic, no LLM. Rows are pre-scoped per creator by callers (routers/upload_intel.py, routers/publications.py, chat/tools.py); work bounded (≤168 rows/creator); sync callers — no async blocking. Rubrics 1/2/3 clean; defects are correctness.
- **[SEV2] upload_intel/timing.py:31-52** — `best_upload_windows` **slices to `top_n` before filtering malformed rows**. Line 31 does `sorted(...)[:top_n]`; the bounds-check `continue` (:39-41) runs inside the already-sliced loop. A malformed row ranking in the top-N is sliced in then skipped → returns **fewer than `top_n`** windows even when valid rows exist beyond the slice. `optimal_gap_hours` (:67-75) already filters-then-slices — inconsistent. | fix: build the valid+coerced list first, then `sorted(..., reverse=True)[:top_n]`. Test: `top_n+1` rows where the highest-activity row is malformed still returns `top_n` valid windows.
- **[SEV2, needs-product-confirmation] upload_intel/timing.py:76-80** — `optimal_gap_hours` maps peaks to linear hour-of-week (`dow*24+hour`, 0–167) and averages only forward gaps within one week, **ignoring week wraparound**. Peaks straddling Sat→Sun (e.g. Sun 01:00=1, Sat 23:00=167) give a 166 h gap when the true cyclic distance is 2 h, overstating cadence when peaks cluster near the boundary. | fix: treat the week as a 168 h circle — also consider wrap gap `(times[0]+168)-times[-1]`; confirm intended metric (min gap vs. mean of cyclic arcs) first.
- **Verdict: NEEDS-WORK** (2× SEV2 correctness; no security/lifecycle issues).

## Rubric coverage (combined)
| Category | analysis | improvement | upload_intel |
|---|---|---|---|
| 1 Resource lifecycle | ok (singleton) | ok (singleton) | n/a |
| 2 Concurrency & scale | ok (worker-thread sync) | ok (worker-thread sync) | ok (≤168 rows, sync) |
| 3 Security & compliance | ok | 1 (SEV2 ZDR/tool version) | ok (rows pre-scoped) |
| 4 Clip-quality | n/a | n/a | n/a |
| 5 Anthropic SDK | ok (marker correctly removed, tokens logged) | 2 (SEV1 pause_turn, SEV2 inert marker) | n/a |
| 6 Cleanliness & typing | 1 (cleanup) | 1 (cleanup) | ok |
| 7 Error handling / API | n/a | n/a | n/a |
| 8 Config & paths | ok | 1 (SEV2 web_search config) | ok |

Totals: **blockers 0 · sev1 1 · sev2 4 · cleanup 2.**

## Module verdict
analysis: clean | improvement: NEEDS-WORK (SEV1 pause_turn) | upload_intel: NEEDS-WORK (2× SEV2 correctness)
