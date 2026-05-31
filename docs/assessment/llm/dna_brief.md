# `dna/brief.py::generate_brief` — LLM efficiency audit

**Date:** 2026-05-31  ·  **Commit baseline:** `80a6557`  ·  **SDK:** `anthropic==0.40` (current GA: 0.105.2 — no breaking changes to this call site)  ·  **Model:** `settings.ANTHROPIC_MODEL = "claude-sonnet-4-6"`

---

## Call shape

| Dimension | Value |
|---|---|
| File | `dna/brief.py:101-173` |
| Trigger | `worker/tasks.py::_build_dna_async` — once per creator at first build + on rebuild |
| Frequency | Low (lifetime + rebuilds per creator; ~1-3 calls per creator total) |
| Model | `claude-sonnet-4-6` |
| `max_tokens` | 2000 (Markdown brief, ≤500 words per system instructions) |
| Streaming | Optional — engages when `task_id` is set (Issue 86 wiring); otherwise non-streaming `.create()` |
| Timeout | 60s (`_ANTHROPIC` module-level client) |
| Cache breakpoint | On last stable block — either `_SYSTEM_INSTRUCTIONS` (~700 tokens) when `stated_identity is None`, or `stated_identity` block when present |
| Cache TTL | 5min (default ephemeral) |
| Web search | Not used |
| Extended thinking | Not used |

## Prompt structure (verified by reading)

```
system = [
  { text: _SYSTEM_INSTRUCTIONS },                  # ~700 tokens, identical across all creators
  { text: stated_identity, cache_control: ephemeral } | (omitted),  # per-creator, ~200-400 tokens when present
  { text: "CREATOR PERFORMANCE DATA: ..." },        # volatile, NEVER cached, ~1500-3000 tokens
]
messages = [
  { role: user, content: "Generate the Creator Brief for '<channel_title>'." }
]
```

Order is correct per 2026 caching standard (stable → cache breakpoint → volatile).

## Findings

### 1. **Cache prefix is at/below the Sonnet 4.6 minimum** (SEV-1 finding — docstring contradicts current Anthropic state)

The module docstring at `dna/brief.py:7` and the prior `docs/DECISIONS.md` 2026-05-29 entry both say:

> "the static prefix is well below Sonnet 4.6's 2048-token minimum cacheable prefix, so the cache does not actually engage"

This is **wrong as of GA 2026 prompt-caching state**. Confirmed by industry-standards-researcher (2026-05-31):
- Sonnet 4.6 minimum cacheable prefix: **1024 tokens** (not 2048).
- Opus 4.6 / Opus 4.7 / Haiku 4.5 minimum: 4096 tokens.

**Implication for this call site:**
- When `stated_identity is None` and the cache breakpoint lands on `_SYSTEM_INSTRUCTIONS` alone (~700 tokens): below the 1024 floor → cache silently does NOT engage. `cache_creation_input_tokens` and `cache_read_input_tokens` both return 0.
- When `stated_identity is not None` and the breakpoint lands on `stated_identity` (concatenated with `_SYSTEM_INSTRUCTIONS` prefix when rendered → ~900-1100 tokens): borderline. Sometimes engages, sometimes doesn't.
- The docstring's premise ("cache doesn't engage") happens to be ~half right, but for the wrong reason and on the wrong condition.

**Fix:** correct the docstring + DECISIONS entry. Measure actual cache hit rate from a representative sample of recent calls — we have the data via the Issue 86 `cache` SSE event since `bbfa3c8` (2026-05-30).

### 2. **Cache reads are rare even when writes engage** (cleanup-severity finding)

DNA build is once-per-creator-lifetime. The 5-minute TTL almost never re-hits — a creator builds, then a different creator builds 10 minutes later. Cache writes cost **1.25×** input price; reads cost 0.1×. **Break-even is 2 requests within TTL** — we hit roughly 0 (each creator's prefix is unique because of `stated_identity`).

**Three options, ranked by impact:**

| Option | Action | Token cost change |
|---|---|---|
| **A (recommended)** | Drop `cache_control` marker entirely from `_build_request`. Stop paying the 1.25× write premium for a cache nobody reads. | -10-15% input-token cost on every DNA build |
| B | Keep the marker but bump TTL to 1h (`{"type": "ephemeral", "ttl": "1h"}`). Cost: 2× write instead of 1.25×. Only justifiable if a creator rebuilds within 1h regularly (rare today). | Worse than A unless rebuild rate is high |
| C | Status quo | 1.25× write premium with no read benefit |

**Recommendation:** Option A. Reconsider if Issue 99/100's onboarding rework drives creators to rebuild DNA more frequently within a session window.

### 3. **`# type: ignore[arg-type]` is now obsolete** (cleanup)

`dna/brief.py:153,157` carry `# type: ignore[arg-type]` on the `system=` and `messages=` kwargs because anthropic 0.40's `TextBlockParam` stub predates the `cache_control` field. The current SDK (0.105.2) types `cache_control` natively — the ignores are no longer needed.

**Blocked behind** the 0.40 → 0.105.2 SDK bump (follow-up issue). Then the ignores come out + we get typed access to the new `usage.cache_creation.ephemeral_5m_input_tokens` / `ephemeral_1h_input_tokens` sub-fields.

### 4. **Streaming path inherits the same prompt structure** (verified clean)

`stream_and_emit` at `dna/brief.py:136-143` reuses `_build_request()`'s output — cache breakpoints are interchangeable between `.create()` and `.stream()` calls. This is correct and documented at `dna/brief.py:131-134`. Issue 86's design intent is honored.

### 5. **No `temperature` / `top_p` / `budget_tokens` / prefill** (verified — Opus 4.7-safe)

Confirmed clean: this call site uses no parameters that 400 on Opus 4.7. Migration path open if we ever flip to Opus 4.7 for this call (not recommended — Sonnet 4.6 is correctly sized for the shape).

### 6. **Token logging is incomplete** (cleanup)

`dna/brief.py:144-150,160-166` logs `input_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, `output_tokens`. **Missing:** new `usage.cache_creation.ephemeral_5m_input_tokens` and `ephemeral_1h_input_tokens` TTL-tier breakdown (current SDK additive change).

**Blocked behind** the SDK bump. Then update both log lines to capture the breakdown so the keep-vs-drop decision in finding #2 has data.

## Latency observation (no historical data, qualitative only)

Based on Issue 86's stated motivating incident ("DNA takes a LONG time" — 3+ min frozen spinner under the PYTHONPATH bug): under healthy conditions, a single DNA brief call ought to land in 8-15s wall-clock on Sonnet 4.6 with `max_tokens=2000` and a ~2-3k-token prompt. Streaming TTFT should be 2-4s.

**Recommended SLO:**
- P50 wall-clock ≤ 12s (streaming path)
- P95 wall-clock ≤ 25s (streaming path)
- TTFT (first token) ≤ 4s

Re-baseline after 1 week of prod data via the Issue-86 `cache` event + Celery task timing logs.

## Module verdict

**clean — 1 finding worth fixing (drop the cache marker), the rest are cleanup or doc fixes.**

The streaming + prompt structure are correct. The cache shape was designed against a now-stale Anthropic constraint; correcting the docstring and dropping the marker is a small mechanical win.
