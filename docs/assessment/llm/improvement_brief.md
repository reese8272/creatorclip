# `improvement/brief.py::generate_improvement_brief` — LLM efficiency audit

**Date:** 2026-05-31  ·  **Commit baseline:** `80a6557`  ·  **SDK:** `anthropic==0.40`  ·  **Model:** `settings.ANTHROPIC_MODEL = "claude-sonnet-4-6"`  ·  **Web search tool:** `web_search_20250305` (STALE — see finding #1)

---

## Call shape

| Dimension | Value |
|---|---|
| File | `improvement/brief.py:55-112` |
| Trigger | `worker/tasks.py::_generate_improvement_brief_async` (Issue 78d — 202 + poll Celery task) |
| Frequency | On-demand per creator. Today: ~1 per creator per week-ish (no scheduled refresh). |
| Model | `claude-sonnet-4-6` |
| `max_tokens` | 2000 (Markdown brief, ≤600 words per system instructions) |
| Streaming | **No** (Issue 92 will add this) |
| Timeout | **120s explicit override** via `_ANTHROPIC.with_options(timeout=120.0)` — correct because `web_search` adds 30-90s |
| Cache breakpoint | On `_SYSTEM_INSTRUCTIONS` (~500 tokens — below the Sonnet 4.6 1024-token floor) |
| Cache TTL | 5min (default ephemeral) |
| Web search | **Yes** — `web_search_20250305` (stale; current GA is `web_search_20260209` with dynamic filtering) |
| Extended thinking | Not used |

## Prompt structure (verified by reading)

```
system = [
  { text: _SYSTEM_INSTRUCTIONS, cache_control: ephemeral },  # ~500 tokens, identical across creators
  { text: "CREATOR ANALYTICS DATA:\n" + analytics_json },     # volatile, ~1500-3000 tokens
]
tools = [{ type: settings.ANTHROPIC_WEB_SEARCH_TOOL, name: "web_search" }]
messages = [
  { role: user, content: "Generate the improvement brief for '<channel>'. Search..." }
]
```

Order is correct per caching standard. **But the cache breakpoint is on a block below the Sonnet 4.6 1024-token floor — cache silently does not engage** (same flavor of bug as DNA brief finding #1).

## Findings

### 1. **`ANTHROPIC_WEB_SEARCH_TOOL=web_search_20250305` is stale** (SEV-2 — concrete latency + cost win)

Current GA web search tool is **`web_search_20260209`**, which adds **dynamic filtering**: Claude writes and executes code to pre-filter search results before they hit the context window. Anthropic documents this as both an accuracy and a token-efficiency win — the model only sees results it has already judged relevant, instead of reading every snippet from every result.

**For this call site specifically:**
- Improvement brief routinely consumes 5-15 search results (creator-niche queries, recent algorithm guidance). Today's `_20250305` ingests them all.
- Dynamic filtering would let Claude triage the search results in a sandboxed pre-step → fewer tokens read into the main context → faster TTFT + lower per-call cost.

**The bump is 1 LOC in `config.py:51` + 1 LOC in `.env.example` + 1 regression test.** Tool API shape is unchanged. No prompt changes needed. **This is Issue 84's recommended shipped win** (Option A from the Phase 1 brief).

### 2. **Cache prefix is below the Sonnet 4.6 minimum** (SEV-1 — same as DNA brief finding #1)

`_SYSTEM_INSTRUCTIONS` is ~500 tokens. Sonnet 4.6's cacheable-prefix floor is 1024 tokens (per current Anthropic prompt-caching docs). **Cache silently does not engage** — `cache_creation_input_tokens` and `cache_read_input_tokens` are both 0 on every call.

The module docstring at `improvement/brief.py:7` says "the static prefix is below Sonnet 4.6's minimum cacheable size, so the cache does not engage." **This one is correct on the facts** (unlike DNA brief which used the wrong number) — but the implication is the same: drop the marker.

**Recommendation:** Same as DNA brief finding #2 — drop the `cache_control` marker entirely. Stop paying the 1.25× write premium for nothing. Once `_SYSTEM_INSTRUCTIONS` ever grows past 1024 tokens (e.g. via richer principle citations or longer guidance), revisit.

### 3. **No streaming — Issue 92 will fix** (acknowledged)

The 202 + poll pattern is correct from a latency-shape perspective (web_search routinely takes 30-90s, can't tie up the API event loop). But the user sees a static "pending" status for ~120s with no incremental signal. Issue 92's scope explicitly includes extending the Issue-86 streaming primitive to this call site.

After Issue 92 lands: same as DNA brief — `generate_improvement_brief_streaming(task_id=...)` routes through `stream_and_emit` so the UI gets `cache` event + `token` deltas in real time.

### 4. **`# type: ignore[typeddict-unknown-key]` is now obsolete** (cleanup)

`improvement/brief.py:80,85` carry `# type: ignore[typeddict-unknown-key]` on `cache_control` and on the `tools=[{type: ..., name: ...}]` shape. Same SDK-stub-lag reason as DNA brief. Removable after the 0.40 → 0.105.2 bump.

### 5. **No `temperature` / `top_p` / `budget_tokens` / prefill** (verified — Opus 4.7-safe)

Same clean migration surface as the other two call sites.

### 6. **Honesty constraint enforced in Python (Markdown disclaimer appended)** (verified clean)

`improvement/brief.py:30-34, 112` — disclaimer is appended by Python, never left to the LLM. Cannot be silently dropped by a prompt-injection or LLM hallucination. This is the right defensive design.

### 7. **Batch API candidate for nightly refresh** (flagged for Issue 93)

If Issue 93 (insights rebuild) introduces a "what changed since last week" diff that requires regular improvement-brief refreshes across the creator base, the Batch API is the right shape:
- 50% cost discount on every call
- ≤1h typical SLA (24h ceiling) — fine for nightly batch
- 1h cache TTL is the documented pairing for batches with shared system prompts

**Not part of Issue 84.** Flagged in Issue 93's Phase-1 to-research list.

## Latency observation

`web_search` adds substantial variance. Typical: 30-60s wall-clock for a brief with 5-10 search results; 90-120s tail when search returns sparse and Claude reformulates queries.

**Recommended SLO:**
- P50 wall-clock ≤ 35s
- P95 wall-clock ≤ 90s
- Web-search dynamic filtering (finding #1) expected to compress P95 by 10-25%

Re-baseline after 1 week of prod data + after web_search bump lands.

## Module verdict

**clean — same shape findings as DNA brief (cache misconfigured, type-ignores obsolete) plus the high-leverage web_search tool upgrade that's the recommended shipped win for Issue 84.**

The 202 + poll pattern was a correct fix in Issue 78d; Issue 92's streaming wrapper completes the UX. No structural concerns.
