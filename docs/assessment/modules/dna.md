# dna — assessed 2026-07-01

Slice: `dna/brief.py`, `dna/builder.py`, `dna/conflict.py`, `dna/embeddings.py`,
`dna/identity.py`, `dna/onboarding.py`, `dna/profile.py`, `dna/__init__.py` (empty).

All best-practice / SDK-behaviour claims below were verified against current official
docs (URLs + date checked inline). Code claims verified by reading (file:line).

## Findings

- [SEV2] dna/embeddings.py:31-37 — `@retry(stop=stop_after_attempt(3), wait=wait_exponential(...))`
  has **no `retry=` predicate**, so tenacity retries on *every* exception, including
  non-transient ones (invalid-request / bad-model / auth failure). Voyage's own client
  retries only transient errors (`RateLimitError`, `ServiceUnavailableError`) with
  exponential jitter — retrying a permanent 4xx three times just adds ~1+2+4s of dead
  backoff before surfacing the real error, and re-issues a doomed request. | fix: scope
  the retry — `from voyageai.error import RateLimitError, ServiceUnavailableError` and add
  `retry=retry_if_exception_type((RateLimitError, ServiceUnavailableError))`; OR drop the
  tenacity wrapper entirely and set `max_retries=3` on the client at dna/embeddings.py:27
  (the SDK's built-in retry is transient-only). Verified: voyageai-python client.py retries
  only `RateLimitError`/`ServiceUnavailableError`
  (https://github.com/voyage-ai/voyageai-python/blob/main/voyageai/client.py, checked
  2026-07-01); tenacity guidance to gate retries on retryable exception types
  (https://python.useinstructor.com/concepts/retrying/, checked 2026-07-01).

- [cleanup] dna/brief.py:123-128 — the `generate_brief` docstring says `stated_identity`
  "is injected as a system block BEFORE the volatile performance corpus and AFTER the static
  instructions." That is **stale**: Issue 224 moved it to the user turn wrapped via
  `wrap_untrusted` (actual code at dna/brief.py:94-100). Because this is the prompt-injection
  boundary for creator-authored text, the drift is misleading on a security-relevant path. |
  fix: update the docstring to state it is passed in the user turn, JSON-wrapped via
  `wrap_untrusted`, never in the system role. (Implementation itself is correct — the
  untrusted-content wrapping is present and right.)

- [cleanup] dna/embeddings.py:21-28 — `_voyage()` lazy `global` singleton is not
  guarded for first-call concurrency; because `_aembed` dispatches via `asyncio.to_thread`,
  two threads racing the first embed can each construct a `voyageai.Client`. Harmless
  (last writer wins, extra client GC'd) but avoidable. | fix: construct the client eagerly
  at module load (as `dna/brief.py:26` does for the Anthropic client) or wrap init in a lock.
  Low priority.

## Notes verified (not findings)

- **Prompt caching (rubric 5).** brief.py deliberately carries NO `cache_control` marker on
  the ~570–650-token static instruction block. Verified correct: Anthropic's minimum
  cacheable prefix is 1,024 tokens for Sonnet 4.x/5 and Opus 4.8, so a marker on a sub-1024
  block is inert (zero cache reads, phantom write premium)
  (https://platform.claude.com/docs/en/build-with-claude/prompt-caching, checked 2026-07-01).
  The volatile per-creator corpus is correctly un-cached. Token usage is logged after every
  call and `record_llm_metric` fires on both the `.create` and streaming paths;
  `warn_if_truncated` fires on both (non-stream at brief.py:211, stream inside
  `worker/anthropic_stream.py:106`). `max_tokens=2000` set on both paths.
- **Voyage usage.** `embed(texts, model="voyage-3.5", input_type="document")` and
  `result.embeddings` match the current API; `input_type="document"` is valid; batch is
  ≤20 texts (top+bottom ≤10 each), well under the 1,000-item / 320K-token voyage-3.5 caps
  (https://docs.voyageai.com/reference/embeddings-api,
  https://docs.voyageai.com/docs/embeddings, checked 2026-07-01).
- **Per-creator isolation (rubric 3).** Every creator-scoped query carries
  `creator_id`: builder.py rank_videos:119-123 + count queries:255/263 + activity:303;
  identity.py get_current/get_history/upsert; profile.py create_draft/confirm_draft/
  get_active/get_version; onboarding.py _has_clip_track_videos:104. The batched IN-queries in
  `_enrich_videos` (builder.py:164-181) are keyed on `video_id`s already filtered to the
  creator, so no cross-tenant path. No token/PII reaches any log line; no virality promise
  (disclaimer at brief.py:32-36; prompt forbids it at :54). Parameterized SQLAlchemy only.
- **Async / blocking (rubric 2).** No sync blocking inside `async def`: builder/identity/
  profile/onboarding are pure `await session.execute`; embeddings offloads the sync Voyage
  SDK + tenacity sleeps via `asyncio.to_thread` (embeddings.py:45). `generate_brief` is a
  sync function (Celery-side), so its `.create()` call is not hidden in an event loop.
- **Bounded work (rubric 2).** rank_videos is capped by `DNA_LONGS_CAP`/`DNA_SHORTS_CAP`;
  enrichment operates on ≤ top+bottom videos; retention rows bounded. No unbounded fetchall.
- **Clip-quality (rubric 4).** DNA ranking is `engagement_rate × recency_weight` (90-day
  half-life, builder.py:28/35-42) — the creator's own signal, exponential recency decay,
  consistent with Principles 6/10/11. Not a clip-start module (no setup-vs-aftermath anchor
  to check here). conflict.py correctly surfaces stated-vs-inferred mismatch as a UI nudge
  rather than silently overriding stated identity (aligns with COMPLIANCE honesty posture).
- **Concurrency correctness.** identity.upsert_identity and profile.confirm_draft both use
  `SELECT ... FOR UPDATE` + partial-unique-index backstop + IntegrityError recover-and-retry
  — idempotent and race-safe.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — clients are singletons (Anthropic eager, Voyage lazy); sessions caller-managed with explicit `commit` flag |
| 2 Concurrency & scale | ok — to_thread offload, bounded queries, no N+1 (IN-batched), FOR-UPDATE locks |
| 3 Security & compliance | ok — per-creator filter on every query; untrusted-identity wrapped; no PII/token logs; no virality promise |
| 4 Clip-quality | ok — recency-decayed, creator-own-data ranking; conflict surfaced not overridden |
| 5 Anthropic SDK | ok — caching correctly omitted (sub-1024 floor, verified); tokens logged; truncation warned; max_tokens set |
| 6 Cleanliness & typing | 2 cleanup — stale docstring (brief.py:123), lazy-singleton race (embeddings.py:21) |
| 7 Error handling / API | n/a (no router in slice) |
| 8 Config & paths | ok — all config via `settings`; no hardcoded paths/secrets |

## Module verdict
NEEDS-WORK (minor) — one SEV2 (un-scoped tenacity retry retries permanent errors in
embeddings.py) plus two cleanups; isolation, async, caching, and concurrency are all sound.
