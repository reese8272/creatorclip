# `clip_engine/scoring.py::score_candidates` — LLM efficiency audit

**Date:** 2026-05-31  ·  **Commit baseline:** `80a6557`  ·  **SDK:** `anthropic==0.40`  ·  **Model:** `settings.ANTHROPIC_MODEL = "claude-sonnet-4-6"`

---

## Call shape

| Dimension | Value |
|---|---|
| File | `clip_engine/scoring.py:140-230` |
| Trigger | `worker/tasks.py::_generate_clips_async` — once per video, only when a DNA brief exists |
| Frequency | **Highest of the three call sites.** Bounded by upload volume × videos-clipped. A creator clipping their backlog of 20 videos = 20 calls; a creator clipping per upload = 1 call per upload. |
| Model | `claude-sonnet-4-6` |
| `max_tokens` | 1200 (JSON array, no prose) |
| Streaming | **No.** `AsyncAnthropic` non-streaming `.create()`. |
| Timeout | 60s |
| Cache breakpoint | On the DNA-brief block (`cache_control: ephemeral, ttl: 1h`) |
| Cache TTL | **1 hour (explicit, correct).** |
| Web search | Not used |
| Extended thinking | Not used |

## Prompt structure (verified by reading)

```
system = [
  { text: _SYSTEM_STATIC + named principles },      # ~400 tokens, identical across all creators
  { text: "CREATOR DNA:\n" + dna_brief,             # ~600-1000 tokens, per-creator stable, cached 1h
    cache_control: ephemeral, ttl: 1h },
]
messages = [
  { role: user, content: candidates JSON },         # volatile per video, ~800-1500 tokens
]
```

**This is the only call site where prompt caching actually pays.** A creator clipping their backlog hits the DNA-brief cache on every video after the first → cache_read at 0.1× of input cost, vs. paying full price every time.

## Findings

### 1. **Cache shape is correct** (verified — no action)

The 1h TTL is the right call given the access pattern: a creator's batch of N videos scored over a span of ~10-60 minutes. The static principles block leads (correct ordering: stable → cache breakpoint → volatile). DNA-brief block carries the breakpoint, never invalidated until a v2 DNA confirms.

Cache write premium: **2× input cost** for the 1h TTL (per current Anthropic pricing). Read cost: 0.1×.

Break-even math: **3 cached reads** (2× write + 0.1× × 3 ≈ 2.3 baseline; vs 3× uncached = 3.0). A creator with ≥4 videos clipped in a 1h window is net positive on caching.

**No change recommended.**

### 2. **Model selection — strong candidate for Haiku 4.5** (SEV-2 finding — opportunity)

This is the highest-frequency call in the system and the task is **structured judgment** (score + cite a named principle + write a 1-sentence reasoning), not creative synthesis. Current pricing (industry-standards-researcher 2026-05-31):

| Model | Input | Output | Per-call cost (1500 in, 1200 out) |
|---|---|---|---|
| `claude-sonnet-4-6` (current) | $3/MTok | $15/MTok | $0.0225 input + $0.0180 output = **$0.0405** |
| `claude-haiku-4-5` | $1/MTok | $5/MTok | $0.0075 input + $0.0060 output = **$0.0135** |

**~67% cost reduction per call.** Haiku 4.5 is "near-frontier" per current Anthropic guidance, designed for high-volume structured tasks. Cache pricing scales proportionally (Haiku floor is 4096 tokens, which we don't currently meet — so caching wouldn't engage at all on Haiku unless we expanded the prefix; that's actually fine for the rare-rebuild case here).

**Risk:** scoring quality is the entire product. A drop in clip-pick precision is unacceptable.

**Path:** A/B eval against `tests/eval/scenarios/*.yaml` (we have 3+ labeled fixtures). Run both models on the same scenarios, compare:
- Top-1 / Top-3 principle agreement
- Setup-start anchor correctness (the load-bearing clip-quality property)
- Score distribution shape (does Haiku flatten or sharpen?)

**Recommendation:** File as Issue (post-84) "Evaluate Haiku 4.5 for clip scoring." Not part of Issue 84's shipped win — too much eval surface area for one issue.

### 3. **No streaming — correct for this shape** (verified clean)

Per-video clip scoring runs in `_generate_clips_async` (worker), not in a request path. The user already sees a 202 + poll flow today. Streaming would add Redis emit cost (~N XADDs per video × videos in batch) for no UX win because the response is consumed by the ranking layer, not surfaced as live text to the user.

**Future:** when Issue 94 (clip-engine transparency) adds a per-clip "why this clip" surface, streaming + co-located scoring + explanation under one cache prefix becomes attractive. Flagged for that issue's Phase-1.

### 4. **JSON parsing fallback to signal scores on JSONDecodeError** (verified clean)

`clip_engine/scoring.py:220-225` — if Claude returns non-JSON, falls back to the cold-start signal score path. Correct degradation. No virality language, principle citation enforced.

### 5. **No `temperature` / `top_p` / `budget_tokens` / prefill** (verified — Opus 4.7-safe)

Same as DNA brief — no Opus-4.7-breaking parameters. Clean migration surface if we ever flip models here.

### 6. **Token logging — same `cache_creation` sub-object gap as DNA brief** (cleanup)

`clip_engine/scoring.py:212-218` logs the same four fields as DNA brief. Missing the TTL-tier breakdown. Blocked behind SDK bump.

This call site is the **most important place to capture the breakdown** because it's where caching actually pays — being able to split 1h-TTL reads vs writes is the cost-savings dashboard's load-bearing data.

## Latency observation

Non-streaming Sonnet 4.6 with `max_tokens=1200` and a ~2-3k-token prompt typically lands in 4-8s wall-clock on the first call (cache write), 2-4s on subsequent calls (cache read). The 1h TTL keeps this fast across a creator's batch.

**Recommended SLO:**
- P50 wall-clock ≤ 4s per video (cache-hit path)
- P95 wall-clock ≤ 10s per video (cache-miss / cold-start path)

Re-baseline after 1 week of prod data.

## Module verdict

**clean — caching is correctly designed and the only material opportunity (Haiku 4.5 migration) needs an eval, not a code change.**

This is the bright spot of CreatorClip's LLM surface: the prompt is well-structured, caching engages as designed, and the cost ceiling is bounded by uploads × videos-clipped which is fundamentally low even at hundreds of creators.
