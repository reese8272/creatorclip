# dna — assessed 2026-05-31 (Wave 4)

Slice: `dna/__init__.py` (empty), `dna/brief.py`, `dna/builder.py`,
`dna/conflict.py`, `dna/embeddings.py`, `dna/identity.py`, `dna/profile.py`.
Wave 4 did NOT touch any file in `dna/` — `git log b4b3400..67fddc9 -- dna/`
is empty and HEAD (`67fddc9`) is the baseline. Wave 4's three-fix batch
landed in `worker/catalog_sync.py`, `routers/onboarding.py`, and
`worker/tasks.py` (Issue 88 regression hotfix + readiness predicate alignment
+ business-event observability) — none of those edits reach `dna/`.
The dna→worker call surface used by `dna/brief.py:134-143`
(`stream_and_emit` import + invocation) is also unchanged at HEAD;
`worker/anthropic_stream.py`'s signature
(`system, messages, tools: list | None = None`) and dict-based
`stream_kwargs` construction still drops `tools` when the caller omits it,
so the brief.py call shape is bit-for-bit identical to Wave 3. This
re-verification therefore confirms (a) the three carry-forward SEV2s
(streaming Redis chattiness, asymmetric `# type: ignore[arg-type]`,
function-local `worker.anthropic_stream` import) are still present at the
exact same line numbers at HEAD, (b) the Wave-2 Issue-84 audit cleanups
(stale 2048-token docstring at `brief.py:7`, missing TTL-tier cache
breakdown in token-log lines at `brief.py:144-150` and `:160-166`,
obsolete `# type: ignore[arg-type]` pair at `brief.py:156-157`) still
reproduce at HEAD and remain blocked behind the SDK bump, (c) the
Issue-98 split state-machine ownership KISS smell between
`dna/profile.py:82-84` and `worker/tasks.py` still stands, and (d) no
new defects were introduced by Wave 4 in this slice. Callers traced:
`worker/tasks.py::_build_dna_async` (streaming + onboarding transitions,
Wave-4 changes outside this slice did not perturb the call shape),
`worker/anthropic_stream.py::stream_and_emit` (Wave-3 `tools` kwarg still
backward-compatible with `dna/brief.py`'s zero-tool call),
`worker/progress.py::sync_emit` (per-token XADD path unchanged).

## Findings

### Re-verified carry-forwards (Wave 1 → Wave 2 → Wave 3 → Wave 4 unchanged)

- [SEV2] dna/brief.py:130-151 (concurrency & scale — sync stream on the
  worker loop) — UNCHANGED at HEAD. `generate_brief` is invoked from
  `_build_dna_async` wrapped in `asyncio.to_thread` (worker/tasks.py), but
  when `task_id is not None` it calls `stream_and_emit`, which iterates the
  sync `client.messages.stream(...)` and routes every `content_block_delta`
  through `worker.progress.sync_emit` — a synchronous redis-py `XADD` per
  token. At `max_tokens=2000` that's up to ~2k synchronous Redis XADDs per
  brief on a single worker threadpool slot. Bounded latency (Redis <1ms LAN)
  but each active build holds one threadpool slot for the full Claude call
  duration; at Celery `worker_concurrency=N` only N concurrent DNA builds
  can stream. | fix: acceptable for v1 (DNA builds are
  once-per-creator-lifetime — see brief.py:7 docstring acknowledging the
  low-frequency call pattern). If the `build_dna` queue depth grows under
  load, batch deltas (emit every K tokens or every K ms — e.g. K=20 cuts
  XADDs ~10× with no UX-perceptible delay). (needs-runtime-confirmation
  under target creator concurrency.)

- [SEV2] dna/brief.py:153-157 (resource lifecycle / Anthropic SDK —
  asymmetric `# type: ignore[arg-type]`) — UNCHANGED at HEAD. The
  `.create()` call carries `# type: ignore[arg-type]` on both `system` and
  `messages`, but the `stream_and_emit(...)` call at brief.py:136-143
  passes the same `list[dict]` shapes WITHOUT a type-ignore. `stream_and_emit`'s
  signature (`worker/anthropic_stream.py:36-45`) still types `system: Any`
  so the asymmetry is currently masked; the moment the SDK bump to 0.105.2
  tightens `_build_request`'s return shape or the wrapper narrows `system`
  to `list[TextBlockParam]`, the call site at brief.py:136-143 will fail
  mypy while brief.py:156-157 stays silently ignored. | fix: either mirror
  `# type: ignore[arg-type]` onto brief.py:136-143 as a stopgap, OR —
  preferred — drop both ignores AND narrow `_build_request`'s return type
  to `tuple[list[TextBlockParam], list[MessageParam]]` once the SDK bump
  lands. Blocked behind the SDK bump per the Issue 84 audit.

- [SEV2] dna/brief.py:134 (resource lifecycle / KISS — function-local
  `from worker.anthropic_stream import stream_and_emit`) — UNCHANGED at HEAD.
  The import lives inside the `if task_id is not None:` branch, making
  `worker.*` a runtime dep of `dna.brief` (layering smell — `dna/` is
  otherwise free of `worker/` imports). The placement defends against an
  import cycle (`worker.tasks` imports `dna.brief.generate_brief`). | fix:
  accept as the lesser evil for v1; add an inline one-line comment
  explaining WHY the import is function-local (cycle defense) so a future
  cleanup pass doesn't "fix" it back to a top-level import that breaks
  worker startup.

- [cleanup] dna/profile.py:82-84 (KISS — split state-machine ownership) —
  UNCHANGED at HEAD. Issue 98 added a `session.get(Creator, creator_id)`
  inside `create_draft` to bump `connected → dna_pending`, but the
  complementary `awaiting_data → dna_pending` mutation in `worker/tasks.py`
  is still owned by the worker. Both transitions are correct under
  SQLAlchemy identity-map sharing (both `session.get` calls return the
  same instance inside the worker's `async with db.AdminSessionLocal()`
  block), but the split — one transition in `dna/profile.py`, the other in
  `worker/tasks.py` — is non-obvious. The next refactor that deletes the
  worker block thinking `create_draft` owns all transitions will silently
  break the `awaiting_data` entry path. | fix: prefer (a) move the
  `awaiting_data → dna_pending` bump into `create_draft` so the function
  owns ALL non-`active`/non-`dna_pending` entry transitions and the
  worker's state-machine line collapses to a comment, OR (b) add an inline
  comment at dna/profile.py:82 pointing to the worker site so the next
  reader sees the full state machine. Preference: (a) — it makes the
  `commit=False` caller contract simpler ("transition handled, just
  commit") and removes hidden cross-module coupling on the onboarding
  state.

### Wave-2 Issue-84 audit findings reproduced at HEAD (still blocked behind SDK bump)

- [cleanup] dna/brief.py:7 (Anthropic SDK — stale cache-floor docstring) —
  Reproduced at HEAD. Module docstring says "Sonnet 4.6's 2048-token
  minimum cacheable prefix"; that floor has been **1024 tokens since
  extended caching GA** in Sonnet 4.6. `_SYSTEM_INSTRUCTIONS` (~250 words
  ≈ ~330 tokens) + an optional `stated_identity` block (Issue 83, up to
  ~400 words ≈ ~530 tokens for a fully-populated profile) is still under
  the 1024-token floor for a typical creator, so cache markers still
  engage but read as zero-token no-ops the vast majority of calls.
  Combined with the Issue 84 observation that DNA build is
  once-per-creator-lifetime, the `cache_control` marker at brief.py:88 is
  dead weight. | fix: (i) correct the docstring number 2048 → 1024 AND
  state the new realistic threshold, OR (ii) **preferred** — drop the
  `cache_control: ephemeral` breakpoint entirely (delete brief.py:85-88)
  and simplify `_build_request` to a single flat system block list. Gated
  on a deliberate decision rather than the SDK bump.

- [cleanup] dna/brief.py:144-150,160-166 (Anthropic SDK — missing TTL-tier
  cache breakdown) — Reproduced at HEAD. Both `logger.info` token-log
  lines emit `cached_read` and `cached_write` but not the new
  `usage.cache_creation.ephemeral_5m_input_tokens` /
  `ephemeral_1h_input_tokens` TTL-tier breakdown the current SDK exposes
  via `usage.cache_creation`. Without the tier breakdown an ops-side
  observer can't distinguish a 5m-tier cache write (the default, cheap)
  from a 1h-tier write (premium). Same gap in
  `worker/anthropic_stream.py:93-100` (unchanged in Wave 4) and the
  `cache` sync_emit path. | fix: blocked behind the 0.40 → 0.105.2 SDK
  bump (Issue 84 follow-up); once landed, extend both `usage.cache_creation`
  accesses to include `cache_creation_5m_input_tokens` and
  `cache_creation_1h_input_tokens` (defensive `getattr` for old SDK
  responses), and mirror the new fields in `stream_and_emit`'s returned
  `usage_dict` + the `cache` sync_emit payload.

### Other carried cleanups (unchanged since 2026-05-30)

- [cleanup] dna/builder.py:292-294 (DRY) — Local `_avg` reimplements
  None-filtering mean used in the aggregate path; single use, KISS-
  acceptable. | fix: leave for v1 — extract on the second caller.

- [cleanup] dna/conflict.py:34-42 (KISS — silent niche coverage gap) —
  `_NICHE_KEYWORDS` only covers 7 of the 15 niche IDs in
  `youtube.categories.NICHE_OPTIONS` (27, 26, 20, 23, 10, 17, 28). The
  branch at conflict.py:82-86 auto-marks the other 8 as "matched" with
  the documented "rather than false-positive on a niche we can't detect"
  rationale. Behavior IS the design; invisibility is the issue. | fix:
  add a one-line comment at the dict citing intentionally-uncovered niche
  IDs and why; or add a test pinning the uncovered set so a future niche
  catalog change is a loud test failure rather than a silent precision
  regression.

- [cleanup] dna/identity.py:202-237 (DRY) — `validate_text`,
  `validate_optional_text`, `validate_list` share the same strip /
  length-check shape. KISS-tolerable at three helpers. | fix: extract a
  single `_check_bounded(value, *, max_chars, label, required: bool)`
  helper if a fourth validation shape lands.

- [cleanup] dna/identity.py:262 (typing — silenced unused import) —
  Trailing `_ = sa` discard-assignment silences "unused import" for
  `sqlalchemy as sa`, kept "for future column-level helpers." Carrying an
  unused import behind a discard-assignment is worse than dropping it and
  re-adding it when the helper lands. | fix: drop `import sqlalchemy as sa`
  and the `_ = sa` line; re-add in the PR that introduces the first
  column-level helper.

### Wave-4 verification: no dna/ touchpoints

- ✅ `git log b4b3400..67fddc9 -- dna/` is empty. Wave-4's three-fix batch
  (`worker/catalog_sync.py` `httpx.ReadTimeout` handling in the Analytics
  retry loop, `routers/onboarding.py` 180s Shorts threshold + catalog
  sync wiring, `worker/tasks.py` business-event observability) does not
  touch any file in this slice. `worker/anthropic_stream.py` is also
  unchanged at HEAD vs Wave 3, so `dna/brief.py:134-143`'s `stream_and_emit`
  call site continues to receive the same `stream_kwargs` shape it did in
  Wave 3 (no `tools=` key inserted).

### Verified-still-fixed since prior assessments

- ✅ Issue 98 — `dna/profile.py::create_draft` bumps `connected →
  dna_pending` (profile.py:82-84) so `confirm_draft`'s `dna_pending →
  active` branch (profile.py:135-136) is reachable from the canonical
  onboarding arc. Regression tests at
  `tests/test_dna_idempotency_integration.py` pin advancement, the
  `dna_pending` no-op, and non-regression from `active`. Mutation is part
  of the caller's transaction (whether `commit=True` or `commit=False`);
  worker's complementary `awaiting_data → dna_pending` branch in
  `worker/tasks.py` still runs.

- ✅ Prior SEV1 (`_build_dna_async` idempotency check-then-act) — still
  intact: `pg_advisory_xact_lock(hashtext(creator_id))` at the top of the
  transaction, re-check under the lock before any paid Anthropic / Voyage
  call, partial UNIQUE on `build_job_id` from migration 0008 as backstop,
  IntegrityError-on-commit treated as idempotent no-op.

- ✅ Prior SEV2 (`_enrich_videos` N+1) — still batched at
  `dna/builder.py:148-196` to exactly 3 IN-queries (transcripts, signals,
  retention) regardless of video count.

- ✅ Prior SEV2 (`rank_videos` unbounded fetch) — still capped at
  `settings.DNA_MAX_CANDIDATE_VIDEOS` (default 500, `.env.example:71`),
  ordered by `published_at DESC NULLS LAST`.

- ✅ Issue 88 diagnostic event (`dna_build_insufficient_data`) — present
  at `dna/builder.py:245-271`. Emits structured fields
  (`total_videos_in_db`, `metered_videos`, `ranked_longs`, `ranked_shorts`,
  `min_longs`, `min_shorts`) so the data-gate/build mismatch is one log
  line away from diagnosis. Readiness predicate
  (`VideoMetrics.engagement_rate.is_not(None)`, no `ingest_status` gate —
  builder.py:115-126) matches the fix Issue 88 shipped — Wave 4's
  follow-up regression hotfix lived in `worker/catalog_sync.py` and did
  not alter this slice's predicate.

- ✅ Streaming-path emission (Issue 86) — unchanged by Wave 4. Cache
  hit/miss + input tokens forwarded as `event: cache` before the first
  generated token via `message_start.usage`
  (anthropic_stream.py:110-124). Each `text_delta` forwarded as
  `event: token`; unknown delta types silently dropped (defensive against
  future SDK additions). The Wave-3 `tools` kwarg is still the only
  recent change to this file and does not affect dna's no-tools call.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — sessions via `async with` (builder reuses caller's session; identity/profile open none of their own); Anthropic singleton (brief.py:21-25), Voyage lazy singleton (embeddings.py:20-27); `_build_dna_async` commits one atomic txn (draft + onboarding state + embeddings); `commit=False` threading correct across `create_draft` + `embed_patterns` + `embed_brief`. Two carried SEV2s (streaming function-local import, asymmetric `# type: ignore`). |
| 2 Concurrency & scale | ok-with-1-SEV2 — every sync call (Voyage `_embed`, Anthropic `.create`/`.stream`) offloaded via `asyncio.to_thread`; no blocking call inside `async def`; `rank_videos` bounded by `DNA_MAX_CANDIDATE_VIDEOS=500`; `_enrich_videos` batched (no N+1). Carried SEV2: streaming path does up to ~2k synchronous Redis XADDs per brief on one worker threadpool slot. pgvector HNSW index on `dna_embeddings.embedding` (migration 0006). |
| 3 Security & compliance | ok — `creator_id` filter on every query (builder.py:119, 249-250, 257-258, 297; profile.py:53, 110-111, 145-149, 168, 184; identity.py:32-35, 45-47, 81); embeddings scoped on insert (embeddings.py:83-89, 119-124); conflict.py operates on already-fetched rows. No token/PII in any `logger.*` (identity.py:125-129,136 logs only creator_id + exception class; brief.py:144-150,160-166 logs only token counts; embeddings.py:63,92,128 logs only counts/creator_id; profile.py:89,160 logs only version + creator_id). No virality promise — disclaimer pinned at brief.py:27-31, system prompt at brief.py:48 explicitly instructs "never promise virality." Parameterized SQL throughout. |
| 4 Clip-quality | partial/ok — recency decay real (`_recency_weight`, λ=ln2/90, builder.py:35-42, 90-day half-life matches CLIPPING_PRINCIPLES.md "recency-decayed reranking"); ranking is against THIS creator's DNA, never generic (engagement_rate × recency_weight, no global baseline). Brief is narrative synthesis (not a per-clip score) so the per-clip numbered-principle citation rule lands on `clip_engine/`, not here — but the brief's five-section structure ("Channel Signature", "What's Driving Views", "Where to Improve", "Optimal Clip Profile", "Shorts Strategy" — brief.py:41-46) maps to Principles #1 (hook in first 3s), #6 (retention curve is ground truth), #10 (native length over generic), and #11 (audience-fit over generic virality). Disclaimer enforcement compliant (brief.py:27-31 + system-prompt instruction at brief.py:48). `conflict.py::detect` correctly surfaces stated-vs-inferred niche mismatch as a UI nudge rather than silently overriding stated intent — matches Principle #11 and the 2026 PReF-class recommender pattern referenced in conflict.py's docstring. |
| 5 Anthropic SDK | ok-with-2-cleanups — caching split correct in `_build_request` (static instructions + stated identity BEFORE `cache_control: ephemeral` breakpoint at brief.py:88, volatile corpus AFTER); cache may engage today since the 1024-token floor (not 2048 as the stale docstring at brief.py:7 claims) but DNA build is once-per-creator-lifetime so reads are vanishingly rare — Issue-84 audit recommendation to drop the marker entirely is the right cleanup. `max_tokens=2000`; tokens + cache_read + cache_creation logged in BOTH the `.create()` and streaming paths (brief.py:144-150, 160-166); streaming path forwards `message_start.usage` as `cache` event before first token (correct per /claude-api guidance). Wave-3 `tools` kwarg on `stream_and_emit` still conditionally forwarded only when non-None — dna's no-tools call shape preserved through Wave 4. Missing TTL-tier breakdown (5m vs 1h ephemeral) — blocked behind 0.40 → 0.105.2 SDK bump. No web-search tool (synthesis-only call — appropriate). |
| 6 Cleanliness & typing | ok-with-cleanup — every signature typed; no TODO/print/debug. Minor: `_ = sa` discard-assignment at identity.py:262; `_avg` DRY duplication at builder.py:292-294; three near-duplicate validators at identity.py:202-237; stale docstring at brief.py:7. |
| 7 Error handling / API | n/a — no routers in this slice; surfaces typed exceptions (`ValueError` on insufficient data / invalid niche / missing draft; `RuntimeError` on Claude empty response; `IntegrityError` caught and treated as idempotent no-op in `upsert_identity` + `confirm_draft`). |
| 8 Config & paths | ok — `MIN_VIDEOS_FOR_DNA=10`, `MIN_SHORTS_FOR_DNA=5`, `DNA_MAX_CANDIDATE_VIDEOS=500`, `VOYAGE_API_KEY`, `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL=claude-sonnet-4-6` all present in `.env.example` with descriptions. No filesystem paths in module. |

## Module verdict

clean — Wave 4 did not touch `dna/` (`git log b4b3400..67fddc9 -- dna/`
is empty; HEAD == baseline 67fddc9) and the adjacent Wave-3 surface
(`worker/anthropic_stream.py`'s `tools` kwarg) is itself unchanged at
HEAD, so the dna→worker streaming path is byte-for-byte identical to
Wave 3. All prior BLOCKER/SEV1 findings remain structurally fixed. The
three carry-forward SEV2s (streaming Redis chattiness, asymmetric SDK
type-ignore, function-local worker import) are unchanged at HEAD and
remain defensible-for-v1 with documented fix paths. The Issue-98 split
state-machine ownership between `dna/profile.py:82-84` and
`worker/tasks.py` is the only durable KISS smell worth a future
refactor pass. The Wave-2 Issue-84 audit findings (stale 2048-token
docstring at brief.py:7, missing TTL-tier cache breakdown in token
logs, obsolete `# type: ignore[arg-type]` pair) all reproduce at HEAD
and are correctly tracked as follow-ups gated on the SDK bump and a
deliberate "drop the cache marker entirely" decision. No new defects
discovered this wave.
