# dna — assessed 2026-05-30

Slice: `dna/__init__.py` (empty), `dna/brief.py`, `dna/builder.py`,
`dna/conflict.py`, `dna/embeddings.py`, `dna/identity.py`, `dna/profile.py`.
Callers traced for the streaming-path delta: `worker/tasks.py::_build_dna_async`
(passes `job_id` to `generate_brief`), `worker/anthropic_stream.py` (the
streaming wrapper this module now calls). Prior-assessment finding deltas
verified against `worker/tasks.py:518-643`, `dna/builder.py:103-189`,
`alembic/versions/0006_vector_and_fk_indexes.py` (HNSW), and
`alembic/versions/0008_*` (partial UNIQUE on `build_job_id`).

## Findings

### Carried forward

- [cleanup] dna/brief.py:35-57,82-90 (Anthropic SDK — cache still inert) —
  the static/volatile split remains structurally correct: `_SYSTEM_INSTRUCTIONS`
  + optional `stated_identity` go BEFORE the `cache_control: ephemeral`
  breakpoint, volatile corpus AFTER (Issue 86 preserved the split inside
  `_build_request`). But `_SYSTEM_INSTRUCTIONS` is still ~250 words and the
  optional identity block is a few hundred more — combined, still under Sonnet
  4.6's 2048-token minimum cacheable prefix for any realistic creator. The
  cache writes/reads will fire as zero-token no-ops. Tokens ARE logged (both
  `.create()` path brief.py:160-166 AND streaming path brief.py:144-150).
  Status unchanged from prior assessment — documented in module docstring +
  docs/DECISIONS.md. | fix: none required; revisit when SDK bump (Issue 84)
  enables 1024-token-floor extended caching, OR when identity blocks routinely
  exceed the floor in production.

- [cleanup] dna/builder.py:256-258 (DRY) — local `_avg` reimplements
  None-filtering mean used across the aggregate path; single use, KISS-
  acceptable. Carried forward; not worth extracting until a second caller
  appears.

### New (Issue 86 + identity/conflict modules)

- [SEV2] dna/brief.py:130-151 (concurrency & scale — sync stream on the worker
  loop) — `generate_brief` is called from `_build_dna_async` wrapped in
  `asyncio.to_thread` (worker/tasks.py:582-588), which is correct — but when
  `task_id is not None`, this function calls `stream_and_emit`, which iterates
  the sync `client.messages.stream(...)` context manager and does a *synchronous*
  `worker.progress.sync_emit` Redis round-trip per token delta
  (worker/anthropic_stream.py:62-69, 100-120). At ~2000 output tokens × one
  XADD round-trip each, that's 2k synchronous Redis hits per brief, on the
  worker thread. Redis is fast (<1ms LAN) so this is bounded latency, not a
  loop-stall (the `to_thread` boundary protects the loop), but every active
  build holds one thread in the worker's thread pool for the full Claude call.
  At Celery `worker_concurrency=N`, only N concurrent DNA builds can stream;
  additional builds queue on the threadpool. | fix: acceptable for v1 given
  DNA builds are low-frequency (once per creator, low overall QPS), but if
  build_dna queue depth grows under load, batch deltas (emit every K tokens
  or every K ms) to cut Redis round-trips by ~10x. (needs-runtime-confirmation
  under target creator concurrency.)

- [SEV2] dna/brief.py:153-157 (resource lifecycle — type-ignore hides a real
  param-shape risk) — both `.create()` and the new `stream_and_emit` are
  passed `system: list[dict]` and `messages: list[dict]` with `# type: ignore`
  on the `.create()` call but NOT on the streaming call (brief.py:142). The
  Anthropic SDK accepts these at runtime (TextBlockParam stub gap, as the
  comment in brief.py:85-87 notes), but the typed-dict signatures of `.create`
  and `.stream` are NOT identical in all SDK versions — adding a type-ignore
  to one call site and not the other will fail mypy the day the SDK is
  bumped (Issue 84). | fix: either add the same `# type: ignore[arg-type]`
  to `stream_and_emit(..., system=system, messages=messages)` at brief.py:136-143,
  OR — better — narrow `_build_request`'s return type to the actual SDK
  TypedDicts (`list[TextBlockParam]`, `list[MessageParam]`) once Issue 84
  bumps anthropic past 0.40 and the stubs are complete.

- [SEV2] dna/brief.py:134 (resource lifecycle — function-local import inside
  hot path) — `from worker.anthropic_stream import stream_and_emit` is
  inside the `if task_id is not None:` branch. This is import-time-once
  thanks to Python's module cache, but it places `worker.*` as a runtime
  dependency of `dna.brief` — a layering smell, since `dna/` is otherwise
  free of `worker/` imports. The original placement is presumably defensive
  against an import cycle (`worker.tasks` imports `dna.brief`), but the
  cleaner shape is to inject the streaming function as a parameter or
  module-level callable so `dna.brief` doesn't reach across the boundary.
  | fix: accept the function-local import as the lesser evil for v1
  (genuine cycle risk), but add a one-line comment explaining WHY it's
  function-local so a future cleanup pass doesn't "fix" it back to a
  top-level import that breaks worker startup.

- [cleanup] dna/conflict.py:34-42 (KISS — niche coverage gap is silent) —
  `_NICHE_KEYWORDS` only has keyword hints for 7 of the 15 niche IDs in
  `youtube.categories.NICHE_OPTIONS` (27, 26, 20, 23, 10, 17, 28). For the
  other 8 (22 People & Blogs, 24 Entertainment, 25 News & Politics, 19
  Travel, 1 Film, 15 Pets, 2 Autos, 29 Nonprofits), the code at
  conflict.py:82-86 auto-marks them as "matched" with the documented
  rationale "rather than false-positive on a niche we can't detect."
  Result: a creator who self-IDs as e.g. "News & Politics" never gets a
  niche-mismatch nudge regardless of what they actually post. The behavior
  IS the design (better silent than wrong), but the gap is invisible —
  there's no log line, no test that pins which niches are covered, nothing
  to remind a future maintainer to backfill. | fix: add a one-line comment
  at the dict citing which niche IDs are *intentionally* uncovered and
  why (e.g. "News/Travel/Pets keyword maps are noisy — silence over false-
  positive"), and either log at module import or assert in a test that
  the uncovered set is the expected one. Drop coverage tests in `tests/`
  for the covered niches so a future expansion is mechanical.

- [cleanup] dna/identity.py:212-237 (DRY) — `validate_text`,
  `validate_optional_text`, and `validate_list` share the same shape
  (strip, length-check, label-into-message). `validate_optional_text` is
  effectively `validate_text` with a None-passthrough. Three small
  near-duplicate helpers are KISS-tolerable, but if a fourth validation
  shape lands (Issue 84 / intake v2 extension), extract a single
  `_check_bounded(value, *, max_chars, label, required: bool)`.
  | fix: leave for v1; flag if a fourth helper is added.

- [cleanup] dna/identity.py:262 (typing — silenced unused import) — the
  trailing `_ = sa` line silences "unused import" for `sqlalchemy as sa`,
  which is imported "for future column-level helpers" per the comment.
  Carrying an unused import behind a discard-assignment is worse than
  dropping the import and re-adding it when the helper actually lands —
  ruff would surface it as a clean diff at that point. | fix: drop the
  `import sqlalchemy as sa` and the `_ = sa` line; add it back in the
  PR that introduces the first column-level helper.

### Verified fixed since prior assessment (commit f6c73ee → HEAD)

- ✅ Prior SEV1 (worker/tasks.py build_dna idempotency check-then-act) —
  `_build_dna_async` now takes `pg_advisory_xact_lock(hashtext(creator_id))`
  at the top of a single AdminSessionLocal transaction (worker/tasks.py:526-529)
  and re-checks the `build_job_id` key UNDER the lock (worker/tasks.py:533-543),
  short-circuiting redeliveries BEFORE any Anthropic/Voyage call. Migration
  0008 added the partial UNIQUE on `build_job_id` as the structural backstop,
  and the IntegrityError-on-commit path at worker/tasks.py:621-630 treats a
  lost race as the idempotent no-op. Fix matches the recommended shape exactly.

- ✅ Prior SEV2 (`_enrich_video` N+1) — `dna/builder.py:142-189` is now
  `_enrich_videos` (plural), batched to 3 IN-queries total (transcripts,
  signals, retention) regardless of video count.

- ✅ Prior SEV2 (`rank_videos` unbounded fetch) — `dna/builder.py:118-119`
  now caps at `settings.DNA_MAX_CANDIDATE_VIDEOS` (default 500, in
  `.env.example:70`), ordered by `published_at DESC NULLS LAST`. Recency-
  weighted ranking already favors recent content, so the cap is a
  monotonic-bounded approximation.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — sessions via `async with` (builder reuses caller's session; identity/profile open none of their own); Anthropic singleton (brief.py:21), Voyage lazy singleton (embeddings.py:23-27); `_build_dna_async` commits one atomic txn; `commit=False` threading correct across create_draft + embed_patterns + embed_brief; HNSW index on `dna_embeddings.embedding` (migration 0006). One SEV2 noted on the streaming import-locality. |
| 2 Concurrency & scale | ok-with-1-SEV2 — every sync call (Voyage `_embed`, Anthropic `.create`/`.stream`) offloaded via `asyncio.to_thread`; no blocking call inside `async def`; rank_videos bounded; _enrich_videos batched (no N+1). New: streaming path does ~2k synchronous Redis XADDs per brief on the worker thread — bounded but consumes one threadpool slot per active build (SEV2, needs-runtime-confirmation under target concurrency). pgvector HNSW index in place. |
| 3 Security & compliance | ok — `creator_id` filter on every query (builder.py:112, 261; profile.py:53, 95, 132, 154, 169; identity.py:32, 45, 81; embeddings scoped on insert; conflict.py operates on already-fetched rows). No token/PII in any `logger.*` (verified: identity.py:126,136 logs only creator_id + class name; brief.py logs only token counts; embeddings.py logs only counts + creator_id). No virality promise (disclaimer brief.py:27-31, prompt brief.py:48 "never promise virality"). Parameterized SQL throughout — no f-string/`%` SQL. |
| 4 Clip-quality | partial/ok — recency decay real (`_recency_weight`, λ=ln2/90, builder.py:36-43); ranking against THIS creator's DNA, never generic; brief is narrative synthesis not a per-clip score so the per-clip numbered-principle citation rule lands on `clip_engine/`. New (Issue 83): `conflict.py` correctly surfaces stated-vs-inferred niche mismatch as a UI nudge rather than silently overriding stated intent — matches the PReF guidance cited in the module docstring and CLIPPING_PRINCIPLES.md #11 (audience-fit over generic virality). |
| 5 Anthropic SDK | ok-with-note — caching split correct in `_build_request` (static instructions + stated identity BEFORE breakpoint, volatile corpus AFTER) but still inert under Sonnet 4.6's 2048-token minimum (`_SYSTEM_INSTRUCTIONS` ~250 words + optional identity block); `max_tokens=2000`; token + cache counts logged in BOTH the `.create()` and streaming paths; streaming path forwards `message_start.usage` as `cache` event before first token (correct per /claude-api guidance). No web-search tool (synthesis only — appropriate). |
| 6 Cleanliness & typing | ok-with-cleanup — every signature typed; no TODO/print/debug. Minor: `_ = sa` discard-assignment in identity.py:262; `_avg` DRY duplication in builder.py:256-258; validator near-duplicates in identity.py:212-237. |
| 7 Error handling / API | n/a — no routers in this slice; surfaces typed exceptions (`ValueError` on insufficient data / invalid niche / missing draft; `RuntimeError` on Claude empty response; `IntegrityError` caught and treated as idempotent no-op in upsert_identity + confirm_draft). |
| 8 Config & paths | ok — `MIN_VIDEOS_FOR_DNA`, `MIN_SHORTS_FOR_DNA`, `DNA_MAX_CANDIDATE_VIDEOS`, `VOYAGE_API_KEY`, `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` all in config.py + `.env.example` with descriptions. No filesystem paths in module. |

## Module verdict

clean — every BLOCKER/SEV1 from the prior assessment is structurally fixed in
the worker and in this module's batched/capped fetch path; the remaining
findings are SEV2 forward-looking concerns (streaming Redis chattiness under
load, SDK type-ignore symmetry, import locality) and cleanup. The new
Issue 83 identity + conflict modules and the Issue 86 streaming refactor are
well-isolated, recency-correct, and compliant.
