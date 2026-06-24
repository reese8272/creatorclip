# knowledge — assessed 2026-06-24

Slice: `knowledge/` — `__init__.py` (empty), `chapters.py`, `hooks.py`,
`thumbnails.py`, `titles.py`, `util.py`. This module is **pure compute**: each
builder takes already-fetched arguments (`dna_brief`, `transcript`,
`channel_title`, retention curves) and calls Claude. It opens **no DB sessions**
and runs **no creator-scoped queries** — per-creator isolation is enforced by the
callers in `worker/tasks.py`, which I re-verified: every `_*_async` task checks
`video.creator_id != cid` and uses `creator.id`-scoped selects
(worker/tasks.py:3187, :3332, :3523, :3702). All four LLM-calling functions are
synchronous and are correctly off-loaded via `asyncio.to_thread(...)` from the
async Celery coroutines, so the blocking Anthropic SDK client never runs on the
event loop (verified worker/tasks.py:3206, :3388, :3414, :3596, :3728). ruff +
mypy both pass clean on the slice.

> Supersedes the 2026-06-16 pass. NOTE: that pass recorded the titles/thumbnails
> `cache_control` markers as "removed (prefix below the 2048 floor)". The code has
> since changed again (Issue 218): the markers are **present and live**, the floor
> is correctly cited as **1024** for Sonnet 4.6, and the comments were rewritten.
> This assessment reflects the current tree.

## Findings
- [SEV2] thumbnails.py:144 — `analyze_thumbnail_patterns` (multimodal vision over
  up to 10 images, `settings.ANTHROPIC_MODEL` = Sonnet) records its usage to
  `logger.info` only: it does NOT call `observability.record_llm_tokens`, AND its
  caller path (worker/tasks.py:3388) never passes the usage to
  `record_llm_usage` — only the second call (`generate_thumbnail_concepts`) is
  billed. The vision call is therefore **un-metered (no Prometheus series) and
  un-billed (no cost-ledger row)**; at scale this is real un-attributed spend. |
  fix: have `analyze_thumbnail_patterns` call `record_llm_tokens(provider="anthropic",
  model=settings.ANTHROPIC_MODEL, input_tokens=response.usage.input_tokens,
  output_tokens=response.usage.output_tokens)` after the call (mirror hooks.py:229),
  and return its usage so the caller bills it via `record_llm_usage`.
- [SEV2] titles.py:230 / thumbnails.py:306 / chapters.py:218 — these three builders
  log token counts via `logger.info` but never call `observability.record_llm_tokens`;
  only hooks.py:229 does. Their task callers DO call `billing.ledger.record_llm_usage`,
  but that writes the DB cost row only — it does NOT increment the Prometheus
  `LLM_TOKENS_TOTAL` metric (confirmed billing/ledger.py:118-153 has no
  `record_llm_tokens` call). Net: the token-rate/cost dashboards see hooks + chat +
  insights but silently under-count titles/thumbnails/chapters. | fix: add one
  `record_llm_tokens(...)` call after each `stream_and_emit` return (model =
  `settings.ANTHROPIC_MODEL` for titles/thumbnails, `_HAIKU_MODEL` for chapters),
  matching hooks.py:229, so billing and observability agree module-wide.
- [SEV2] chapters.py:22 + hooks.py:27 — `_HAIKU_MODEL = "claude-haiku-4-5-20251001"`
  is hardcoded in both files (used at chapters.py:208, hooks.py:216/231). The ID is
  valid, but config.py defines only `ANTHROPIC_MODEL` (no hook/chapter override), so
  rotating or A/B-testing the Haiku model on these two surfaces requires a code change
  and redeploy — inconsistent with every other call site, which reads
  `settings.ANTHROPIC_MODEL`. (Tracked Issue 221.) | fix: add
  `ANTHROPIC_MODEL_CHAPTERS` and `ANTHROPIC_MODEL_HOOK_ANALYSIS` to `config.Settings`
  (default `"claude-haiku-4-5-20251001"`), document both in `.env.example`, replace the
  two hardcodes, and log the decision in docs/DECISIONS.md.
- [cleanup] titles.py:41 + thumbnails.py:44 — `_DISCLAIMER` constants are defined but
  referenced nowhere (verified repo-wide). The sibling brief modules (dna/brief.py:169,
  analysis/brief.py:180, improvement/brief.py:162) all append their `_DISCLAIMER` to the
  returned text; titles.py's module docstring (line 16) still claims "The honesty
  disclaimer is always appended by Python" — false for this module. Honesty is NOT
  violated: prompts mandate hedged per-item rationales and the frontend renders standing
  "estimates grounded in your own data, not guarantees" notices on these surfaces
  (Editor.tsx, Analysis.tsx, Pricing.tsx, Settings.tsx). So this is dead code + a stale
  docstring, not a compliance gap. (Tracked Issue 109.) | fix: delete both constants and
  correct the titles.py docstring, or actually append them to the done payload.
- [cleanup] titles.py:220 / thumbnails.py:296 / hooks.py:212 / chapters.py:204 —
  `_ANTHROPIC.with_options(timeout=120.0|60.0)` replaces the client's
  `httpx.Timeout(X, connect=10.0)` with a flat scalar, silently loosening the *connect*
  timeout from 10s to the full read budget. Under load a stuck TCP connect can hold the
  worker thread for up to 120s instead of failing fast at 10s. (Tracked Issue 82, async
  wave 2.) | fix: drop the `with_options(...)` and pass `_ANTHROPIC` directly (its
  module-level timeout already encodes connect=10s + the right read budget), or pass a
  full `httpx.Timeout(120.0, connect=10.0)`.
- [cleanup] chapters.py:24 / hooks.py:29 / thumbnails.py:31 / titles.py:31 — four
  separate module-level `Anthropic(...)` singletons with the same api_key and
  near-identical timeout/retry config (DRY). Each IS a singleton (not per-call), so no
  leak — just duplication. | fix: build one shared client and import it; keep per-call
  `.with_options` (or the fix above) for the longer-budget callers.
- [cleanup] titles.py:107 / thumbnails.py:175 — `_build_request` /
  `_build_concepts_request` annotated `-> tuple` (bare) where the shape is
  `tuple[list[dict], list[dict], list[dict]]` (the `(system, tools, messages)` contract).
  (Tracked Issue 109.) | fix: annotate the precise tuple type.
- [cleanup] titles.py:38 (`_GENERATE_N = 10`) + hooks.py:36 (`TRANSCRIPT_EXCERPT_S =
  60.0`) — dead constants, never referenced (the "10" lives in the prompt text; the 60.0
  is passed literally by the caller). hooks.py:172-173 also computes a "Xpp below median"
  line guarded only by `(creator_median_at_drop or 0)`, which would mislead if median is
  None while `retention_drop_at_s` is not — unreachable from the sole caller today but
  the `float | None` signature permits it. (Tracked Issue 109.) | fix: delete the dead
  constants; guard `creator_median_at_drop is not None` before emitting the median tail.

## Notes verified (no finding)
- Anthropic SDK: `max_tokens` set on every call (512–2000). Prompt caching is correct
  and live where it pays off: titles.py:138 / thumbnails.py:216 carry a `cache_control`
  ephemeral-1h breakpoint on the DNA block (~1,550-tok prefix > Sonnet 4.6's 1024 floor,
  so titles→hooks→thumbnails within a creator session share the cached prefix);
  chapters/hooks intentionally omit the marker because their ~175/~900-tok prefixes are
  below Haiku 4.5's 4096 floor (documented inline, Issue-135 precedent). The vision call
  has no caching but its *result* is Redis-cached 24h by the caller, so re-calls are rare
  — acceptable. Structured output is prompt-enforced JSON + `json.loads` (repo-wide
  convention, matches dna/brief.py), not the SDK tool-schema mode — not flagged.
- Security/compliance: no token/PII in any `logger.*` line (only token counts + ids). No
  OAuth/`decrypt()` in scope. Strong prompt-injection posture — `UNTRUSTED_CONTENT_POLICY`
  in every static prefix, and `wrap_untrusted()` JSON-encodes attacker-influenceable
  `stated_identity` and moves it to the user turn (Issue 224/225). No virality promise.
- Concurrency: no `time.sleep`/`requests.`/`subprocess` anywhere in the slice; no `async
  def` in the module; all blocking LLM work off-loaded via `to_thread`. Builders are
  pure/stateless and safe to re-run, meeting the re-runnability the module owes (task-level
  retry double-billing is a worker/ concern, not this slice). Inputs are bounded
  (`_SEGMENT_MAX_CHARS`, `_DNA_BRIEF_MAX_CHARS`, 10-image cap, 20-curve cap at caller).
- Config & paths: `ANTHROPIC_API_KEY`/`ANTHROPIC_MODEL`/`ANTHROPIC_WEB_SEARCH_TOOL` all in
  config.py + `.env.example` with descriptions. No filesystem paths in the module.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — clients are module-level singletons (4-way DRY cleanup); no DB/file/subprocess handle in module |
| 2 Concurrency & scale | ok — sync LLM client off-loaded via to_thread by every caller; no blocking call/async def in module; inputs bounded (connect-timeout-loosening `with_options` noted as cleanup) |
| 3 Security & compliance | ok — no PII/token logged; strong injection hardening; no creator-scoped query lives here (isolation verified at callers) |
| 4 Clip-quality | n/a (advisory title/thumbnail/hook/chapter surfaces, not the clip-extraction path) |
| 5 Anthropic SDK | 3 findings — un-metered+un-billed vision call, missing `record_llm_tokens` in 3 builders, hardcoded `_HAIKU_MODEL`; caching/max_tokens otherwise correct & live |
| 6 Cleanliness & typing | 5 cleanup — dead `_DISCLAIMER` x2 + stale docstring, dead `_GENERATE_N`/`TRANSCRIPT_EXCERPT_S` + median guard, duplicate clients, `with_options` connect loosening, bare `-> tuple` |
| 7 Error handling / API | n/a (no FastAPI routes; library raises ValueError/SDK errors for Celery retry — consistent contract) |
| 8 Config & paths | ok — all config in `.env.example`; no paths (model-override gap captured as the `_HAIKU_MODEL` SEV2) |

## Module verdict
NEEDS-WORK — no BLOCKER and no SEV1; the module is secure, isolation-safe, and
non-blocking, and prompt caching is now correct. But the thumbnail vision call is
un-metered AND un-billed, three builders skip the Prometheus token metric while only
hooks records it, and the Haiku model is hardcoded on two surfaces — three SEV2s that
leave LLM cost/observability inconsistent across the module, plus five low-risk cleanups.
