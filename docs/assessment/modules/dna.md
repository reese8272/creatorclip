# dna — assessed 2026-05-29

Slice: `dna/__init__.py`, `dna/brief.py`, `dna/builder.py`, `dna/embeddings.py`,
`dna/profile.py`. Cross-references into `worker/tasks.py` (caller), `models.py`,
and `alembic/versions/0001_initial_schema.py` are for tracing only — owned elsewhere.

## Findings

- [SEV1] dna/brief.py:64 — `generate_brief` calls the **synchronous** Anthropic
  client (`_ANTHROPIC.messages.create`, the sync `Anthropic` constructed at
  brief.py:18) and is awaited-by-position from the `async def _build_dna_async`
  at worker/tasks.py:342. Per worker/tasks.py:6-8 every coroutine runs on the
  worker process's **singleton event loop**, so this blocks that loop for the
  whole LLM round-trip (up to the 60s `httpx.Timeout`). Any other coroutine on
  that worker (other `run_async` dispatches, the asyncpg pool) is stalled for the
  duration — p99 latency / throughput collapse under concurrent DNA builds.
  | fix: wrap the call in `await asyncio.to_thread(generate_brief, patterns, channel_title)`
  in worker/tasks.py, or switch `_ANTHROPIC` to `anthropic.AsyncAnthropic` and
  `await` it. Prefer `to_thread` since the SDK call is otherwise sync end-to-end.

- [SEV1] dna/brief.py:62-72 — prompt caching is structurally ineffective. The
  `cache_control: ephemeral` block (brief.py:71) wraps `_SYSTEM_TEMPLATE.format(corpus=corpus)`,
  but `corpus` (brief.py:57-61) is the per-creator, per-version performance JSON —
  unique on every call. The cache is therefore written every time and read ~never
  (≈0% hit rate), so the mandatory-caching architecture rule buys nothing and the
  module docstring's claim (brief.py:4-5 "same cache hit is reused by the clip
  scorer") is false — the scorer cannot share a creator-specific cache entry.
  | fix: split the system into two blocks — a stable instruction block (the static
  template text) marked `cache_control: ephemeral`, and the volatile `corpus`
  passed as the user message (or an uncached trailing system block). Only the
  stable prefix should carry `cache_control`. Re-verify with `/claude-api`.

- [SEV1] worker/tasks.py:346 + dna/profile.py:50-72 — `build_dna` is not
  idempotent under at-least-once delivery. `create_draft` derives the new version
  from `SELECT max(version)+1` (profile.py:50-53). If the task's single commit
  succeeds but the broker redelivers (visibility timeout / worker lost), the next
  run computes a higher max and inserts a **second draft row** (v_n+1) — duplicate
  DNA drafts and a second paid Anthropic + Voyage spend per redelivery. The
  rollback-on-failure reasoning in the docstring (tasks.py:310-317) only covers
  the pre-commit failure path, not post-commit redelivery. (Caller owns the task
  wrapper, but the version-assignment logic lives in this module.)
  | fix: make the build idempotent on a stable key — e.g. guard on a
  `(creator_id, source_fingerprint)` so a redelivery is a no-op when an identical
  draft already exists, or pass an explicit target version derived from a job key
  rather than `max()+1`. Add a test that fires `build_dna` twice for one creator
  and asserts exactly one new draft row + one Anthropic call.

- [SEV1] alembic/versions/0001_initial_schema.py:233 / models.py:320 — the
  `dna_embeddings.embedding` Vector(1024) column has **no HNSW or IVFFlat index**
  (only the btree `ix_dna_embeddings_creator_id`). Every similarity query over
  this column is a sequential `<->` scan, O(rows) per creator-corpus — fine at
  seed scale, dies as embeddings accumulate across hundreds of creators. This
  module is the writer (embeddings.py:72-80); the read path lives elsewhere, but
  the missing index originates from the shared model/migration this module's data
  depends on. | fix: add a migration `CREATE INDEX CONCURRENTLY` (outside a txn)
  HNSW index on `embedding` with the op class matching the query distance
  (`vector_cosine_ops` for cosine; voyage-3.5 vectors are normalized). Document
  the chosen distance op in `docs/SOT.md`.

- [SEV2] dna/builder.py:223-224 + dna/builder.py:137-161 — `_enrich_video` issues
  per-video round trips (`session.get(Transcript)`, `session.get(Signals)`, and a
  `RetentionCurve` SELECT) inside a `for v in top_all + bottom_all` loop — a
  classic N+1. With up to 20 videos (top 10 + bottom 10) that is up to 60 serial
  awaited queries per DNA build. | fix: batch-load — one `select(Transcript)
  .where(Transcript.video_id.in_(ids))`, one for `Signals`, and one
  `select(RetentionCurve).where(RetentionCurve.video_id.in_(ids))` grouped in
  memory by `video_id`, then enrich from the dicts.

- [SEV2] dna/builder.py:201-202 — `kind` is compared to string literals
  (`v["kind"] == "long"` / `"short"`) after being stored as `video.kind.value`
  (builder.py:124). This couples the builder to the exact enum *value* strings; a
  rename of the `VideoKind` enum value silently drops every video from both
  buckets and trips the data gate with a misleading "insufficient data" error.
  | fix: compare against the enum (`v["kind"] == VideoKind.long.value` with a
  module constant, or carry the enum member rather than `.value` in the dict).

- [SEV2] dna/embeddings.py:70-83 — `embed_patterns` runs the synchronous Voyage
  `_embed` (decorated with tenacity `@retry`, embeddings.py:29-31) directly in an
  `async def` on the worker's singleton loop (same loop-blocking class as the
  brief call, compounded by up to 3 retry sleeps of `wait_exponential(min=1,max=10)`
  — up to ~20s of blocking `time.sleep` on the loop). Same for `embed_brief`
  (embeddings.py:106). | fix: `await asyncio.to_thread(self._embed, ...)`, or use
  Voyage's async client. The tenacity backoff must not sleep on the event loop.

- [SEV2] dna/profile.py:75-113 — `confirm_draft` reads confirmed + draft rows and
  flips statuses without row locking; two concurrent confirmations for one creator
  can both pass the "supersede confirmed" step and promote two drafts to
  `confirmed`, violating the documented "only one confirmed per creator" invariant
  (profile.py:5) — there is no DB constraint enforcing it (models.py:305 only
  uniques `(creator_id, version)`). | fix: either add a partial unique index
  `CREATE UNIQUE INDEX ... ON creator_dna (creator_id) WHERE status='confirmed'`,
  or `SELECT ... FOR UPDATE` the creator's DNA rows at the top of `confirm_draft`.

- [cleanup] dna/builder.py:189 — `build_patterns` returns a 6-tuple
  `(dict, list, list, float|None, str|None, float|None)`; positional unpacking at
  worker/tasks.py:332-339 is fragile and untyped at the boundary. | fix: return a
  small `@dataclass DnaBuildResult` (or `NamedTuple`) so fields are named and typed.

- [cleanup] dna/builder.py:226 — `_avg(vals: list)` uses a bare `list` (untyped
  element) where the values are `list[float | None]`. | fix: annotate
  `vals: list[float | None] -> float | None`.

- [cleanup] dna/builder.py:58 / dna/builder.py:87 — `retention_rows: list` and
  `activity_rows: list` parameters are untyped element-wise; CLAUDE.md mandates
  full typing on every signature. | fix: type as `list[RetentionCurve]` /
  `list[AudienceActivity]`.

- [cleanup] dna/profile.py:128 — `get_active` falls back to the latest *draft*
  when no confirmed profile exists, but the docstring is the only place this
  behavior is recorded; the clip scorer/ranking honesty-threshold logic depends on
  knowing whether DNA is confirmed vs draft. Not a defect in this module, but flag:
  callers (routers/clips.py:60, routers/improvement.py:57) should distinguish.
  | fix: have `get_active` return the status alongside, or expose a
  `get_confirmed()` for paths that must not rank on an unconfirmed draft.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — sessions via `async with` in caller; embeddings/profile honor `commit=False` for atomic txn; clients are singletons (Anthropic module-level; Voyage lazy singleton) |
| 2 Concurrency & scale | 5 findings — sync LLM/Voyage calls block the singleton worker loop (SEV1+SEV2); N+1 in `_enrich_video` (SEV2); no pgvector index (SEV1) |
| 3 Security & compliance | ok — every query creator-scoped (`WHERE creator_id` at builder.py:110, 231; profile.py:51,84,95,119,137); parameterized SQL only; no token/PII in logs (logs creator_id UUID only, allowed); brief prompt carries honesty disclaimer + no-virality instruction (brief.py:24-28,43) |
| 4 Clip-quality | partial — DNA scoring is per-creator (recency-weighted engagement, builder.py:131) and cites Native-length / best-region principles structurally; `optimal_clip_len_s` feeds the setup-anchored engine elsewhere. No principle-citation string emitted in this module (brief text is freeform) — acceptable since this is DNA synthesis, not per-clip scoring |
| 5 Anthropic SDK | 1 SEV1 — caching present but ineffective (volatile corpus inside cached block); token usage IS logged (brief.py:82-88); `max_tokens` set; sync client blocks loop |
| 6 Cleanliness & typing | 4 cleanups — 6-tuple return, untyped `list` params/locals, draft-fallback ambiguity |
| 7 Error handling / API | n/a (no router in slice; data-gate raises `ValueError`, brief raises `RuntimeError` on empty completion — both handled by caller) |
| 8 Config & paths | ok — all config via `settings` (MIN_VIDEOS_FOR_DNA, MIN_SHORTS_FOR_DNA, VOYAGE/ANTHROPIC keys), all present in `.env.example`; no filesystem paths in module |

## Module verdict
NEEDS-WORK — no cross-tenant leak and isolation is solid, but two SEV1 scale defects
(sync LLM/Voyage calls block the worker's singleton event loop; prompt caching is
ineffective), a non-idempotent version-assignment on Celery redelivery, and a missing
pgvector index must be fixed before this survives hundreds of concurrent creators.
