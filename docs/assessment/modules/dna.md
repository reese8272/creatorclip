# dna — assessed 2026-05-29

Slice: `dna/__init__.py` (empty), `dna/brief.py`, `dna/builder.py`,
`dna/embeddings.py`, `dna/profile.py`. Callers traced: `worker/tasks.py`
(`build_dna` / `_build_dna_async`), `routers/creators.py`, `routers/clips.py`,
`routers/improvement.py`. Schema/index traced to `models.py` + `alembic/versions/`.

## Findings

- [SEV1] worker/tasks.py:423-430 (dna idempotency contract) — `_build_dna_async`
  checks `build_job_id` existence in one session, releases it, then inserts the
  draft in a *separate* session, with NO `pg_advisory_xact_lock` and NO unique
  index on `creator_dna.build_job_id` (only the non-unique `ix_creator_dna_build_job_id`,
  alembic 0005:42). Two concurrent at-least-once redeliveries of the same Celery
  task_id can both pass the `already is None` guard and both run the build —
  duplicate *paid* Anthropic brief + Voyage embedding calls and two draft rows
  (versions max+1 and max+2) for one job. The `uq_dna_creator_version` constraint
  only stops a version collision, not the duplicate job. Issue 63 hardened
  `confirm_draft` but left the *build* check-then-act unguarded.
  | fix: take `await session.execute(select(func.pg_advisory_xact_lock(hashtext(creator_id))))`
  at the top of the single build transaction and move the `build_job_id` existence
  check inside that same transaction; OR add a partial-unique index
  `uq_dna_build_job_id (build_job_id) WHERE build_job_id IS NOT NULL` and catch
  `IntegrityError` on commit to make the redelivery a no-op. Add a regression test
  firing two builds with the same job_id concurrently asserting exactly one draft.
  (needs-runtime-confirmation under real concurrent redelivery)

- [SEV2] dna/builder.py:223-224 (concurrency & scale — N+1) — `_enrich_video` is
  awaited sequentially in a Python loop over `top_all + bottom_all` (up to ~20
  videos), each call issuing 3 round-trips (`session.get(Transcript)`,
  `session.get(Signals)`, `select(RetentionCurve)`), i.e. up to ~60 sequential
  awaits per build. Bounded (top/bottom capped) so not a leak, but a serial N+1
  that lengthens build latency under load.
  | fix: batch — one `select(Transcript).where(Transcript.video_id.in_(ids))`, one
  for `Signals`, one `select(RetentionCurve).where(video_id.in_(ids)).order_by(video_id, timestamp_s)`,
  then map by `video_id` in memory. Removes the per-video round-trips.

- [SEV2] dna/builder.py:107-115,134 (concurrency & scale — unbounded fetch) —
  `rank_videos` does `result.all()` over every `done`+metered video for the
  creator with no LIMIT, then sorts and the caller keeps the full set in memory
  (`videos_analyzed = len(ranked)`). For a large back-catalog channel this loads
  the entire video history into the worker per build.
  | fix: per-creator one-shot so blast radius is bounded, but cap the working set
  (order by `published_at DESC` in SQL and `.limit(settings.DNA_MAX_VIDEOS)`,
  default ~500) and add the cap to config + `.env.example`.
  (needs-runtime-confirmation for realistic catalog sizes)

- [cleanup] dna/brief.py:35-48,64-83 (Anthropic SDK — caching no-op) — the
  static/volatile split is structurally correct (cached `cache_control: ephemeral`
  prefix vs uncached per-creator corpus block after it), but `_SYSTEM_INSTRUCTIONS`
  is ~250 words (well under Sonnet 4.6's 2048-token minimum cacheable prefix; model
  confirmed `claude-sonnet-4-6` in config.py:35), so the cache never engages — a
  correct-but-inert no-op. The docstring (brief.py:6-8) already states this and
  points to docs/DECISIONS.md. No behavior bug; recorded so the assessment notes
  the cache as inert. Token usage IS logged after the call with cache read/write
  counts (brief.py:85-91) — compliant. | fix: none required; leave as documented,
  or drop the breakpoint to avoid implying an active cache.

- [cleanup] dna/builder.py:226-228 (DRY) — local `_avg` reimplements None-filtering
  mean used across the aggregate path; minor, single use.
  | fix: reuse a shared numeric-mean helper if one exists; else leave (KISS).

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — sessions via `async with db.AsyncSessionLocal()`; Anthropic singleton (brief.py:21), Voyage lazy singleton (embeddings.py:20-27); build writes one atomic commit; `commit=False` threading correct |
| 2 Concurrency & scale | 2 findings — serial N+1 in `_enrich_video`; unbounded `rank_videos` fetch. Sync LLM/Voyage calls correctly offloaded via `asyncio.to_thread` (embeddings.py:73,109; brief at tasks.py:448); singleton worker loop |
| 3 Security & compliance | ok — every query filters `creator_id` (builder.py:111,231; profile.py:53,96; embeddings scoped on insert); no token/PII in any `logger.*`; no virality promise (disclaimer brief.py:27-31, prompt brief.py:48); parameterized SQL |
| 4 Clip-quality | partial/ok — recency decay real (`_recency_weight`, λ=ln2/90, builder.py:35-42); scored against THIS creator's DNA, not generic; brief is narrative synthesis not a per-clip score, so the numbered-principle-citation rule lands on clip_engine — no defect here |
| 5 Anthropic SDK | ok-with-note — caching split correct but inert (<2048-token prefix); `max_tokens=2000` set; token + cache counts logged after call (brief.py:85-91); no web-search tool (not intended for synthesis) |
| 6 Cleanliness & typing | ok — signatures typed; no TODO/print/debug; minor `_avg` DRY note |
| 7 Error handling / API | n/a — no routers; surfaces `ValueError`/`RuntimeError` for callers to map |
| 8 Config & paths | ok — `MIN_VIDEOS_FOR_DNA`/`MIN_SHORTS_FOR_DNA`/`VOYAGE_API_KEY`/`ANTHROPIC_MODEL` in config + `.env.example` with descriptions; no filesystem paths in module |

## Module verdict
NEEDS-WORK — well-isolated, recency-correct, and compliant, but the build_dna
idempotency check is a check-then-act with no advisory lock or unique backstop on
`build_job_id`, so concurrent Celery redelivery can double-run a paid build; the
serial N+1 enrichment is a secondary latency cost.
