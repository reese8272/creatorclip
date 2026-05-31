# CreatorClip — LLM Efficiency Assessment (Issue 84)

**Date:** 2026-05-31  ·  **Commit baseline:** `80a6557`  ·  **SDK:** `anthropic==0.40` (current GA: 0.105.2)  ·  **Model setting:** `ANTHROPIC_MODEL=claude-sonnet-4-6` (single source of truth across 3 call sites)

## Verdict — **HEALTHY with one stale config + one structural cache fix**

All three Claude call sites use Sonnet 4.6 today. Prompt structures are correct per 2026 caching standards (stable → cache breakpoint → volatile). **No Opus 4.7-breaking parameters anywhere** — `temperature`, `top_p`, `budget_tokens`, assistant-turn prefills, and `client.count_tokens()` are all absent across the surface. The SDK is 65+ minor versions behind GA but carries zero breaking changes to our call sites.

**The two findings that matter:**

1. **`ANTHROPIC_WEB_SEARCH_TOOL=web_search_20250305` is stale.** Current GA is `web_search_20260209` with dynamic filtering. 1-LOC config bump → improvement brief gets faster + cheaper for free.
2. **Cache markers on DNA brief + improvement brief don't engage** because the static prefixes are below Sonnet 4.6's 1024-token minimum. Both pay the 1.25× write premium for zero reads. Drop the markers OR (eventually) expand the static prefixes past 1024.

The one place where caching actually pays — `clip_engine/scoring.py` per-video scoring with 1h TTL on the DNA brief — is correctly designed and load-bearing for the cost ceiling.

---

## Per-call-site summary

| Call site | Model | Frequency | Streaming? | Cache engages? | Findings |
|---|---|---|---|---|---|
| `dna/brief.py::generate_brief` | Sonnet 4.6 | 1-3 per creator lifetime | Optional (Issue 86) | **No** (prefix < 1024) | Drop cache marker; correct stale docstring |
| `clip_engine/scoring.py::score_candidates` | Sonnet 4.6 | N per creator clip batch | No | **Yes** (1h TTL) | Caching correctly designed. Haiku 4.5 migration opportunity (needs eval). |
| `improvement/brief.py::generate_improvement_brief` | Sonnet 4.6 + web_search | On-demand per creator | No (Issue 92 will add) | **No** (prefix < 1024) | **Bump web_search to `_20260209`**; drop cache marker |

Full per-call-site detail with line references in `dna_brief.md`, `clip_scoring.md`, `improvement_brief.md`.

---

## Ranked register (Layer 1)

| Sev | Finding | Where | Backed fix | Lands in |
|---|---|---|---|---|
| **SEV-2** | `web_search_20250305` is stale; dynamic filtering disabled | `config.py:51`, `.env.example:12` | Bump default to `web_search_20260209`; add regression test asserting the improvement brief request body uses the new string | **Issue 84 — shipped this issue** |
| SEV-1 | Cache prefix below 1024-token Sonnet 4.6 floor — silent no-op on DNA brief + improvement brief | `dna/brief.py:60-98`, `improvement/brief.py:74-83` | Drop the `cache_control` markers on both call sites. Save the 1.25× write premium for zero read benefit. Update docstrings to reflect the correct 1024-token floor. | Follow-up issue (capture in DECISIONS) — needs SDK bump first to measure pre/post cache_creation tokens |
| SEV-1 | SDK 0.40 → 0.105.2 — 65 minor versions stale | `requirements.txt`, all 3 call sites | Bump SDK; remove all `# type: ignore[arg-type]` / `[typeddict-unknown-key]` on `cache_control` blocks (TextBlockParam types it natively now); add `usage.cache_creation.ephemeral_5m_input_tokens` / `ephemeral_1h_input_tokens` to token logs in all 3 call sites | Follow-up issue — non-trivial regression surface, deserves its own assess cycle |
| SEV-2 | Clip scoring on Sonnet 4.6 — Haiku 4.5 is ~67% cheaper for the structured-judgment shape | `clip_engine/scoring.py:198` + `config.py:50` (model is single source today) | (a) Refactor to per-call-site model settings `ANTHROPIC_MODEL_DNA`, `ANTHROPIC_MODEL_CLIP_SCORING`, `ANTHROPIC_MODEL_IMPROVEMENT_BRIEF` (default all to current); (b) A/B eval Haiku 4.5 vs Sonnet 4.6 against `tests/eval/scenarios/*.yaml`; (c) flip if Top-1 principle agreement + setup-start correctness hold | Follow-up issue — eval surface area justifies separate scope |
| cleanup | Cache pricing docs reference 2048-token Sonnet floor (wrong) | `dna/brief.py:7`, `docs/DECISIONS.md` 2026-05-29 entry | Correct to 1024 tokens. Reference industry-standards-researcher 2026-05-31 finding. | Included in Issue 84 docs update |
| cleanup | Streaming on improvement brief | `improvement/brief.py` | Add `task_id` kwarg mirror of `dna/brief.py::generate_brief` | **Issue 92 — already scoped** |
| cleanup | Pipeline candidate: co-locate clip scoring + per-clip explanation under one Claude call | `clip_engine/scoring.py` + `routers/clips.py` | Single call returns scores + reasoning + "why not" for skipped candidates → saves N round-trips for Issue 94's transparency surface | **Issue 94 Phase-1 should research** |
| cleanup | Batch API candidate for nightly improvement-brief refresh | `worker/tasks.py::_generate_improvement_brief_async` | 50% cost discount via `client.messages.batches.create()`; 1h cache TTL is the documented pairing | **Issue 93 Phase-1 should research** |

---

## Recommended SLOs (provisional)

To be re-baselined after 1 week of prod data via the Issue-86 `cache` SSE event + Celery task timing logs. These are derived from Anthropic's streaming-default guidance + observed worker behavior, not measured.

| Call site | TTFT P50 | TTFT P95 | Wall-clock P50 | Wall-clock P95 |
|---|---|---|---|---|
| DNA brief (streaming) | ≤ 4s | ≤ 8s | ≤ 12s | ≤ 25s |
| Clip scoring (non-streaming, cache-hit) | n/a | n/a | ≤ 4s per video | ≤ 10s per video |
| Improvement brief (post web_search bump) | ≤ 8s | ≤ 15s | ≤ 30s | ≤ 75s |

Improvement brief P95 expected to compress 10-25% from finding #1 (dynamic filtering reduces web-search token tail).

---

## Shipped this issue

**Win A — `web_search_20260209` bump** (~5 LOC + 1 test). Smallest surface, lowest risk, ships measurable value. Captured in `docs/DECISIONS.md` 2026-05-31 (Wave 2 / Issue 84) entry with the migration rationale.

The remaining ranked-register items become follow-up issues to be filed:
- **Issue (TBD) — Anthropic SDK bump 0.40 → current.** Tracks remove type-ignores + add `cache_creation` TTL-tier logging.
- **Issue (TBD) — Drop unproductive cache markers on DNA + improvement brief.** Sequence after SDK bump so we can measure before/after.
- **Issue (TBD) — Per-call-site model settings + Haiku 4.5 A/B for clip scoring.** Eval-heavy; deserves its own issue scope.

Issues 93 and 94 will inherit the co-location + Batch API recommendations into their own Phase-1 research.

---

## Industry standards verified (2026-05-31)

- **Anthropic Python SDK:** latest GA `0.105.2`. No breaking changes between 0.40 and current that affect our 3 call sites. `client.count_tokens()` was removed in v0.39 (replaced by `/v1/messages/count_tokens` REST endpoint) — we don't use it.
- **Prompt caching minimums:** Sonnet 4.6 = **1024 tokens** (not 2048); Opus 4.6/4.7 = 4096; Haiku 4.5 = 4096.
- **Cache TTLs:** Two options only — 5min ephemeral (1.25× write, 0.1× read) and 1h ephemeral (2× write, 0.1× read). No 24h.
- **Web search tool:** `web_search_20260209` is GA with dynamic filtering; `web_search_20250305` still supported but no filtering. Pricing $10 per 1k searches + standard token costs.
- **Extended thinking:** Adaptive mode (`{type: "adaptive"}`) recommended for Opus 4.7+; legacy `budget_tokens` form still works on Sonnet 4.6 + Opus 4.6 but deprecated. **None of our call sites use thinking — no migration concern.**
- **Batch API:** GA. 50% cost. "Most batches within 1 hour," 24h ceiling. 1h cache TTL is documented pairing.
- **Model selection (May 2026):**
  - Opus 4.7 — most complex reasoning, $5/$25 per MTok, 128K output ceiling, **128K output cap requires streaming**
  - Sonnet 4.6 — balanced, $3/$15 per MTok, 64K output
  - Haiku 4.5 — fastest near-frontier, $1/$5 per MTok, 64K output

Full research record from industry-standards-researcher in this session's transcript.

---

## Module verdict

**clean — efficient as designed; one stale config bump shipped; the structural improvements (SDK bump, cache-marker drop, Haiku 4.5 eval) become well-scoped follow-up issues with measured priorities.**

The LLM surface is the **cheapest** part of CreatorClip's production cost ceiling today. The clip-scoring 1h cache is the load-bearing reason — every other improvement is incremental. Future cost-scaling risk lives in Issue 92's streaming Redis-XADD volume (already flagged SEV-2 in the worker module assess), not in the LLM surface itself.
