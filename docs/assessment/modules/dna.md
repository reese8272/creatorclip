# dna — assessed 2026-05-31

Slice: `dna/__init__.py` (empty), `dna/brief.py`, `dna/builder.py`,
`dna/conflict.py`, `dna/embeddings.py`, `dna/identity.py`, `dna/profile.py`.
Callers traced: `worker/tasks.py::_build_dna_async` (streaming + onboarding
transitions), `worker/anthropic_stream.py`. Wave-1 delta verified against
`dna/profile.py:52-90` (Issue 98 `connected → dna_pending` bump),
`worker/tasks.py:567-636` (atomic txn, complementary `awaiting_data →
dna_pending` branch), and `tests/test_dna_idempotency_integration.py:147-251`
(three regression tests pinning the new transition + non-regression of
`dna_pending` and `active`).

## Findings

### New this wave (Issue 98 + carry-forwards)

- [SEV2] dna/brief.py:130-151 (concurrency & scale — sync stream on the worker
  loop) — UNCHANGED from prior assessment. `generate_brief` is called from
  `_build_dna_async` wrapped in `asyncio.to_thread`, but when `task_id is not
  None` it calls `stream_and_emit`, which iterates the sync
  `client.messages.stream(...)` and does a synchronous `worker.progress.sync_emit`
  Redis round-trip per token delta. At ~2000 output tokens × one XADD each,
  that's 2k synchronous Redis hits per brief on a worker thread. Bounded latency
  (Redis <1ms LAN) but each active build consumes one threadpool slot for the
  full Claude call. At Celery `worker_concurrency=N`, only N concurrent DNA
  builds can stream; additional builds queue on the threadpool. | fix:
  acceptable for v1 (DNA builds are low-frequency, once per creator). If the
  `build_dna` queue depth grows under load, batch deltas (emit every K tokens
  or every K ms) to cut Redis round-trips ~10×. (needs-runtime-confirmation
  under target creator concurrency.)

- [SEV2] dna/brief.py:153-157 (resource lifecycle — asymmetric type-ignore) —
  UNCHANGED. `.create()` carries `# type: ignore[arg-type]` on both `system`
  and `messages`, but the `stream_and_emit(...)` call at brief.py:136-143
  passes the same `list[dict]` shapes WITHOUT type-ignore. Anthropic SDK 0.40's
  TextBlockParam stub predates `cache_control`, so both accept this at runtime;
  the asymmetry will fail mypy the moment the SDK bump (Issue 84) tightens the
  streaming stub. | fix: either mirror `# type: ignore[arg-type]` onto the
  streaming call at brief.py:136, OR — preferred — narrow `_build_request`'s
  return type to the SDK TypedDicts (`list[TextBlockParam]`,
  `list[MessageParam]`) once Issue 84 lands.

- [SEV2] dna/brief.py:134 (resource lifecycle — function-local import) —
  UNCHANGED. `from worker.anthropic_stream import stream_and_emit` lives inside
  the `if task_id is not None:` branch, making `worker.*` a runtime dep of
  `dna.brief` (layering smell — `dna/` is otherwise free of `worker/` imports).
  The placement defends against an import cycle (`worker.tasks` imports
  `dna.brief`). | fix: accept as the lesser evil for v1; add a one-line comment
  explaining WHY it's function-local so a future cleanup doesn't "fix" it back
  to a top-level import that breaks worker startup.

- [cleanup] dna/profile.py:82-84 (KISS — silent state-machine semantics) —
  Issue 98 added a `session.get(Creator, creator_id)` mid-transaction
  `create_draft` to bump `connected → dna_pending`. The transition is correct
  and the regression tests at `tests/test_dna_idempotency_integration.py:147,
  205, 229` pin the full state matrix (advance from `connected`, idempotent
  from `dna_pending`, non-regression from `active`). The worker's
  complementary `awaiting_data → dna_pending` mutation at `worker/tasks.py:629`
  remains the ONLY path that handles `awaiting_data`. That's correct under
  identity-map sharing (both `session.get` calls return the same instance)
  but the split — one transition in `dna/profile.py`, the other in
  `worker/tasks.py` — is non-obvious and easy to break in a future refactor
  (e.g. someone deletes the worker block thinking `create_draft` handles all
  transitions). | fix: either (a) move the `awaiting_data → dna_pending` bump
  into `create_draft` so the function owns BOTH non-`active`/non-`dna_pending`
  entry transitions, OR (b) add an inline comment at profile.py:82 pointing to
  `worker/tasks.py:629` so the next reader sees the full state machine.
  Preference: (a) — it makes the `commit=False` caller contract simpler
  ("transition handled, just commit") and removes a hidden coupling between
  modules.

- [cleanup] dna/profile.py:82 (resource lifecycle — extra round-trip in
  `commit=False` callers) — `create_draft` now issues `session.get(Creator,
  creator_id)` even when the caller (e.g. `_build_dna_async`) already fetched
  the same Creator row at `worker/tasks.py:567`. SQLAlchemy's identity map
  serves it from cache (no second SQL round-trip) when the same session is
  used — verified by tracing the worker session lifecycle — so this is a
  no-cost no-op in the current call graph. But a future caller that passes a
  fresh session into `create_draft` would issue an extra SELECT. | fix: leave
  for v1; the identity-map cache makes this free in practice. If a future
  caller pattern emerges with a fresh session, accept an optional `creator:
  Creator | None = None` parameter to skip the lookup.

### Carried forward (unchanged since 2026-05-30)

- [cleanup] dna/brief.py:35-57,82-90 (Anthropic SDK — cache still inert) —
  static/volatile split structurally correct: `_SYSTEM_INSTRUCTIONS` +
  optional `stated_identity` go BEFORE the `cache_control: ephemeral`
  breakpoint, volatile corpus AFTER (Issue 86 preserved the split inside
  `_build_request`). But `_SYSTEM_INSTRUCTIONS` is ~250 words + optional
  identity block — combined still under Sonnet 4.6's 2048-token minimum
  cacheable prefix for any realistic creator. Cache writes/reads fire as
  zero-token no-ops. Tokens ARE logged on both paths (brief.py:144-150,
  160-166). | fix: none required; revisit when SDK bump (Issue 84) enables
  1024-token-floor extended caching, or when identity blocks routinely exceed
  the floor.

- [cleanup] dna/builder.py:292-294 (DRY) — local `_avg` reimplements
  None-filtering mean used across the aggregate path; single use, KISS-
  acceptable. Carried forward; not worth extracting until a second caller
  appears.

- [cleanup] dna/conflict.py:34-42 (KISS — silent niche coverage gap) —
  `_NICHE_KEYWORDS` only has keyword hints for 7 of the 15 niche IDs in
  `youtube.categories.NICHE_OPTIONS` (27, 26, 20, 23, 10, 17, 28). For the
  other 8 the code at conflict.py:82-86 auto-marks them "matched" with the
  documented rationale "rather than false-positive on a niche we can't detect."
  Behavior IS the design (better silent than wrong), but invisibility risks
  future maintainer confusion. | fix: add a one-line comment at the dict
  citing which niche IDs are *intentionally* uncovered and why; either log at
  module import or assert in a test that the uncovered set is the expected
  one.

- [cleanup] dna/identity.py:212-237 (DRY) — `validate_text`,
  `validate_optional_text`, `validate_list` share the same strip/length-check
  shape. KISS-tolerable at three helpers; extract a single
  `_check_bounded(value, *, max_chars, label, required: bool)` if a fourth
  validation shape lands (Issue 84 intake v2). | fix: leave for v1.

- [cleanup] dna/identity.py:262 (typing — silenced unused import) — trailing
  `_ = sa` line silences "unused import" for `sqlalchemy as sa`, kept "for
  future column-level helpers." Carrying an unused import behind a
  discard-assignment is worse than dropping it and re-adding it when the
  helper actually lands. | fix: drop `import sqlalchemy as sa` and the
  `_ = sa` line; re-add in the PR that introduces the first column-level
  helper.

### Verified fixed since prior assessment (commit f6c73ee → HEAD 74431e7)

- ✅ Issue 98 — `dna/profile.py::create_draft` now bumps
  `connected → dna_pending` so `confirm_draft`'s `dna_pending → active` branch
  is reachable from the canonical onboarding arc. Three regression tests
  (`tests/test_dna_idempotency_integration.py:147-251`) pin advancement, the
  `dna_pending` no-op, and non-regression from `active`. Verified the mutation
  is part of the caller's transaction (whether `commit=True` or `commit=False`)
  and that the worker's complementary `awaiting_data → dna_pending` branch at
  `worker/tasks.py:629` still runs.

- ✅ Prior SEV1 (`_build_dna_async` idempotency check-then-act) — still
  intact: `pg_advisory_xact_lock(hashtext(creator_id))` at
  `worker/tasks.py:548-551`, re-check under the lock at
  `worker/tasks.py:555-565`, partial UNIQUE on `build_job_id` as backstop,
  IntegrityError-on-commit treated as idempotent no-op at
  `worker/tasks.py:643-652`.

- ✅ Prior SEV2 (`_enrich_video` N+1) — still batched at
  `dna/builder.py:148-196` to 3 IN-queries (transcripts, signals, retention)
  regardless of video count.

- ✅ Prior SEV2 (`rank_videos` unbounded fetch) — still capped at
  `settings.DNA_MAX_CANDIDATE_VIDEOS` (default 500, `.env.example:71`),
  ordered by `published_at DESC NULLS LAST`.

- ✅ Issue 88 diagnostic event (`dna_build_insufficient_data`) — present at
  `dna/builder.py:245-271`. Emits structured fields
  (`total_videos_in_db`, `metered_videos`, `ranked_longs`, `ranked_shorts`,
  `min_longs`, `min_shorts`) so the data-gate/build mismatch is one log line
  away from diagnosis, not a code bisect.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — sessions via `async with` (builder reuses caller's session; identity/profile open none of their own); Anthropic singleton (brief.py:21), Voyage lazy singleton (embeddings.py:23-27); `_build_dna_async` commits one atomic txn (draft + onboarding state + embeddings); `commit=False` threading correct across `create_draft` + `embed_patterns` + `embed_brief`; HNSW index on `dna_embeddings.embedding` (migration 0006). New Issue 98 mutation correctly inside the caller's transaction. Two carried SEV2s (streaming import locality, asymmetric type-ignore). |
| 2 Concurrency & scale | ok-with-1-SEV2 — every sync call (Voyage `_embed`, Anthropic `.create`/`.stream`) offloaded via `asyncio.to_thread`; no blocking call inside `async def`; `rank_videos` bounded by `DNA_MAX_CANDIDATE_VIDEOS`; `_enrich_videos` batched (no N+1). Carried SEV2: streaming path does ~2k synchronous Redis XADDs per brief on the worker thread (bounded but consumes one threadpool slot per active build). pgvector HNSW index in place. |
| 3 Security & compliance | ok — `creator_id` filter on every query (builder.py:119, 249, 257, 297; profile.py:53, 110, 145, 168, 184; identity.py:32, 45, 81; embeddings scoped on insert; conflict.py operates on already-fetched rows). No token/PII in any `logger.*` (identity.py:125,136 logs only creator_id + class name; brief.py logs only token counts; embeddings.py logs only counts + creator_id; profile.py logs only version + creator_id). No virality promise — disclaimer pinned at brief.py:27-31, system prompt at brief.py:48 explicitly instructs "never promise virality." Parameterized SQL throughout. |
| 4 Clip-quality | partial/ok — recency decay real (`_recency_weight`, λ=ln2/90, builder.py:36-43, 90-day half-life matches CLIPPING_PRINCIPLES.md "recency-decayed reranking"); ranking is against THIS creator's DNA, never generic (engagement_rate × recency_weight, no global baseline). Brief is narrative synthesis (not a per-clip score) so the per-clip numbered-principle citation rule lands on `clip_engine/`, not here — but the brief structure cites observable patterns ("Channel Signature", "What's Driving Views", "Optimal Clip Profile", "Shorts Strategy") that align with Principles #1 (hook in first 3s), #6 (retention curve), #10 (native length), and #11 (audience-fit). Disclaimer enforcement compliant. `conflict.py` correctly surfaces stated-vs-inferred niche mismatch as a UI nudge rather than silently overriding stated intent — matches CLIPPING_PRINCIPLES.md #11. |
| 5 Anthropic SDK | ok-with-note — caching split correct in `_build_request` (static instructions + stated identity BEFORE breakpoint, volatile corpus AFTER) but still inert under Sonnet 4.6's 2048-token minimum (`_SYSTEM_INSTRUCTIONS` ~250 words + optional identity block); `max_tokens=2000`; token + cache counts logged in BOTH the `.create()` and streaming paths (brief.py:144-150, 160-166); streaming path forwards `message_start.usage` as `cache` event before first token (correct per /claude-api guidance). No web-search tool (synthesis only — appropriate). |
| 6 Cleanliness & typing | ok-with-cleanup — every signature typed; no TODO/print/debug. Minor: `_ = sa` discard-assignment in identity.py:262; `_avg` DRY duplication in builder.py:292-294; validator near-duplicates in identity.py:212-237. |
| 7 Error handling / API | n/a — no routers in this slice; surfaces typed exceptions (`ValueError` on insufficient data / invalid niche / missing draft; `RuntimeError` on Claude empty response; `IntegrityError` caught and treated as idempotent no-op in `upsert_identity` + `confirm_draft`). |
| 8 Config & paths | ok — `MIN_VIDEOS_FOR_DNA`, `MIN_SHORTS_FOR_DNA`, `DNA_MAX_CANDIDATE_VIDEOS`, `VOYAGE_API_KEY`, `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` all in config.py + `.env.example` with descriptions. No filesystem paths in module. |

## Module verdict

clean — Wave-1 Issue 98 fix is correct, atomically transactional with the
existing `_build_dna_async` writes, and exhaustively regression-tested across
the four `OnboardingState` values. The split state-machine ownership
(`connected → dna_pending` in `dna/profile.py`, `awaiting_data → dna_pending`
in `worker/tasks.py`) is the only new fragility worth noting and is logged
as a SEV2-adjacent KISS cleanup, not a defect. All BLOCKER/SEV1 from prior
assessments remain structurally fixed. Remaining findings are carry-forward
SEV2s (streaming Redis chattiness, SDK type-ignore symmetry, worker import
locality) and cleanup.
