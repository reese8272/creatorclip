# llm_caching_concurrency (cross-cutting focus) — assessed 2026-06-24

Cross-cutting sweep of the whole Python codebase on three axes: (1) Anthropic/LLM
usage, (2) caching layers (Redis + in-process), (3) concurrency & scale. Scope is
every LLM call site + every cache/lock + the async/Celery/pool machinery, not one
package.

**Live verification (One Rule):** confirmed against platform.claude.com/docs/prompt-caching
on 2026-06-24:
- Minimum cacheable prefix: **Sonnet 4.6 = 1,024 tok**, Sonnet 4.5 = 1,024, Opus 4.8 = 1,024,
  Opus 4.6/4.5 = 4,096, **Haiku 4.5 = 4,096**, Haiku 3.5 = 2,048.
- Cache write multipliers: 5-min TTL = 1.25×, **1-h TTL = 2×**; cache read = 0.1×.
- **1-hour TTL cache is GA** on the Claude API (explicit breakpoints only).
The project's default model is `ANTHROPIC_MODEL="claude-sonnet-4-6"` → 1,024-tok floor.
The codebase's own DECISIONS/docstrings cite 1,024 for Sonnet 4.6, which is correct.

---

## Findings

- [SEV2] clip_engine/scoring.py:261 — the `cache_control {"ttl":"1h"}` marker sits on the
  DNA system block, but the **cacheable prefix is below Sonnet 4.6's 1,024-tok floor, so the
  marker is INERT** (zero cache reads ever). Measured: block-1 static prefix (UNTRUSTED policy
  ~170 tok + clip instructions + 11 principle names) ≈ **308 tok**; the DNA brief is generated
  with a hard "under 500 words" cap (dna/brief.py) ≈ ≤665 tok and is NOT additionally capped
  here. Worst-case prefix ≈ **985 tok**; typical (≈300-word brief) ≈ **720 tok** — both under
  1,024. This is the **highest-volume LLM call in the product** (one per scored video), so the
  intended 10× repeat-scoring saving is never realized. Worse, the usage ledger then bills a
  **phantom 2× cache-WRITE premium** (`cache_write_multiplier=2.0`, scoring.py:295) against a
  cache that produced nothing — over-charging the creator's cost estimate. The in-code comment
  (scoring.py:248-252) and `docs/DECISIONS.md` both *assert* the marker engages; it does not.
  | fix: either (a) drop the `cache_control` marker on this path and remove the 2.0 multiplier
  (honest: this prefix is too short to cache — same call class as improvement/brief.py, which
  correctly documents "below the floor, cache does not engage"); OR (b) make the prefix clear
  1,024 by moving the larger stable scoring rubric into block-1 and raising the DNA cap so
  block1+DNA ≥ ~1,150 tok, then keep the marker. Add a test asserting
  `usage.cache_read_input_tokens > 0` on the 2nd identical-DNA scoring call (a token-count
  fixture) so an inert marker can't silently regress again.

- [SEV2] analysis/brief.py:118 — same inert-marker class. DNA block is capped at
  `_DNA_BRIEF_MAX_CHARS = 1000` (~250 tok) and block-1 `_SYSTEM_INSTRUCTIONS` ≈ **410 tok**, so
  the cached prefix ≈ **660 tok** — below the 1,024 floor for Sonnet 4.6. The docstring
  (analysis/brief.py:111-114) claims "Block 1 + block 2 clears Sonnet 4.6's 1024-token floor";
  that claim is false at this DNA cap. Marker pays the 2× write tier for nothing on repeat
  video-analysis calls. | fix: raise `_DNA_BRIEF_MAX_CHARS` toward 3000 (matching titles.py/
  hooks.py/thumbnails.py, which DO clear the floor — their block1 ≈ 703 tok + 3000-char DNA
  ≈ 750 tok ≈ 1,450 tok prefix) so the prefix clears 1,024, OR drop the marker and document it
  as a low-frequency uncached call. Confirm with the same cache-read>0 token assertion.

- [SEV2] clip_engine/scoring.py:253 / chat/intake.py:188 — these two LLM calls run the **sync
  vs async client correctly**, but note `scoring.score_candidates` is `async` and awaits
  `_ANTHROPIC.messages.create` (AsyncAnthropic) directly — good — yet the per-candidate
  `compute_features` CPU work is already offloaded via `asyncio.to_thread` (scoring.py:212).
  No blocking-call defect here; flagging only that scoring runs in the API/worker async path —
  verify under load it is invoked from the Celery `generate_clips` task (it is: worker/tasks.py
  `_generate_clips_async`), not a request handler, so the 60s Anthropic round-trip can't stall
  the API loop. (needs-runtime-confirmation that no router awaits score_candidates inline.)

- [cleanup] knowledge/titles.py:134 / knowledge/thumbnails.py / knowledge/hooks.py — these
  carry the *correct* claim (prefix ≈ 1,450 tok clears 1,024) but the asserted "~1,550 tokens"
  figure is ~100 tok optimistic vs the measured ~1,450; harmless (still clears) but the docstring
  number should match reality so the next reader trusts it. | fix: update the comment to
  "≈1,450 tok (clears 1,024)" or run a token-count fixture and pin the real number.

- [cleanup] clip_engine/scoring.py:277 — the log line emits `cached_write_1h` via
  `getattr(_cache_creation, "ephemeral_1h_input_tokens", 0)`. Given the marker is inert on this
  path (see SEV2 above), this field is always 0 — the log advertises a 1h-cache tier that never
  fires, which will mislead anyone reading token logs to debug spend. | fix: resolves itself
  once the SEV2 inert-marker is fixed; if option (a) (drop marker) is chosen, drop this field too.

## Rubric / axis coverage

| Axis / Category | Status |
|---|---|
| **AXIS 1a — prompt caching above floor** | **2 SEV2**: scoring.py + analysis/brief.py markers inert (prefix < 1,024). titles/thumbnails/hooks clear the floor; improvement/brief.py correctly self-documents as below-floor/uncached; insights.py (Haiku, 4,096 floor) correctly OMITS the marker (documented Issue 138/140). |
| AXIS 1b — token usage logged every call | ok — **every** call site logs usage (`logger.info "... tokens: in=.. out=.."`) AND wires `record_llm_tokens`/`increment_usage`/`record_llm_usage`. No observability gap found across 11 modules. |
| AXIS 1c — max_tokens set / not truncating | ok — every call sets `max_tokens` (256–2000); structured-JSON callers (scoring 1200, titles 2000) have headroom; chat caps at `CHAT_MAX_TOKENS=1500` by design. |
| AXIS 1d — model IDs current / from config | ok — all use `settings.ANTHROPIC_MODEL` / `settings.ANTHROPIC_WEB_SEARCH_TOOL`. The one hardcoded id (`routers/insights.py:458 _HAIKU_MODEL="claude-haiku-4-5-20251001"`) is a deliberate, documented Haiku pin with a comment; acceptable but would be cleaner in config. |
| AXIS 1e — structured output / tool use | ok — intake.py strict-schema `propose_profile` tool w/ self-correction round + `is_error` tool_result; chat/runner agentic loop bounded by `CHAT_MAX_TOOL_ITERATIONS`, final round forces `tools=None`. Correct per SDK pattern. |
| AXIS 1f — web-search where live research intended | ok — `web_search` tool wired in titles.py, improvement/brief.py (and the streaming path passes `tools=` through — Wave-3 Fix A closed the drop-the-tool SEV1). |
| **AXIS 2 — cache key creator-scoped (BLOCKER class)** | **clean** — `thumbnail_patterns:{creator.id}`, lock `thumbnail-patterns-lock:{creator.id}`, `retrain:{cid}`, `catalog-sync:{cid}`, `sse:count:{creator_id}`. No shared/cross-tenant key. DB-keyed insight cache filters on `creator_id`+`video_id`+`dna_version`. |
| AXIS 2 — TTL set & sane | ok — patterns 24h, SSE stream/owner 1h, lock 130s (> one 120s vision round-trip). |
| AXIS 2 — single-flight on expensive LLM | ok — `_compute_patterns_single_flight` per-creator lock (NX+EX), compare-and-delete release, poll-then-fall-through. insights.py uses a DB-row cache that short-circuits the LLM entirely. |
| AXIS 2 — fail-open vs fail-closed | ok and correct: progress/cache paths fail-OPEN (observational), so a Redis blip never 500s a paid endpoint or drops a task; rate-limit/idempotency paths fail-CLOSED via Postgres constraints. |
| **AXIS 3 — pool math** | ok by inspection: app engine `pool_size=15 + max_overflow=5 = 20`/pod under a 25-conn PgBouncer sidecar; admin/worker engine `2+2=4`. PgBouncer txn-pooling + `prepare_threshold=None` (psycopg3) documented. `pool_pre_ping` + `pool_recycle=1800`. (needs-load-evidence — the Locust run, per scale-checklist.) |
| AXIS 3 — no sync/blocking in async on hot path | **clean** — grep found NO `requests.*`, `time.sleep`, or `subprocess.*` inside any `async def`. All ffmpeg/transcribe `subprocess.run` live in Celery-only modules (clip_engine/render.py, ingestion/audio.py, youtube/ingest.py — none contain `async def`), each with an explicit `timeout=`. Sync Stripe SDK + sync Anthropic stream + Voyage are all offloaded via `asyncio.to_thread`. |
| AXIS 3 — shared async engine/redis bound to right loop | ok — `db.recreate_engine()` on `worker_process_init` (post-fork); httpx `_http.client()` and `progress._async_client()` are lazy, loop-aware, rebuild-on-mismatch singletons. |
| AXIS 3 — Celery idempotency under at-least-once | ok — `acks_late=True` + `task_reject_on_worker_lost=True` + `visibility_timeout=3600 > time_limit` invariant; money/media paths idempotent on stable keys: `UNIQUE(video_id)` (deduct), `UNIQUE(stripe_session_id)` (grant), `ClipPublication.task_id` (publish no-double-post), `pg_advisory_xact_lock` + re-check-under-lock (DNA build), `pg_try_advisory_lock` (sweeps/poll/retrain) with unlock on every path. |
| AXIS 3 — limiter not doing sync round-trip on loop | ok — slowapi `Limiter(storage_uri=REDIS_URL)` with real Redis; `creator_key` reads pre-stamped `request.state.creator_id` (no per-call JWT decode); limits are per-creator on authed routes, per-IP only on the webhook + unauth fallback. |
| AXIS 3 — timeouts on every external client | ok — Anthropic clients `httpx.Timeout(60–120, connect=10)` + `max_retries`; youtube `_http` 60/connect5; Stripe `STRIPE_TIMEOUT_S=10`; Redis sync+async `socket_timeout=2.0`; transcription job + http timeouts; ffmpeg subprocess timeouts. |

## Module verdict
NEEDS-WORK — two SEV2 inert-cache markers (clip_engine/scoring.py:261, analysis/brief.py:118)
that waste the intended 10× prompt-cache saving on real LLM paths AND mis-bill a phantom 2×
write premium, both contradicting their own in-code/DECISIONS claims; no BLOCKER — cache keys
are all creator-scoped, no sync blocking call on any async hot path, Celery money/media tasks
are idempotent, pools/timeouts/limiter are sound (pool sizing still wants the Locust evidence).
