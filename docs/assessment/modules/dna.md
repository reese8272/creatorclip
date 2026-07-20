# dna — assessed 2026-07-20

Slice: `dna/brief.py`, `dna/builder.py`, `dna/conflict.py`, `dna/embeddings.py`,
`dna/identity.py`, `dna/onboarding.py`, `dna/profile.py`, `dna/__init__.py` (empty).

Prior assessment: 2026-07-01. Diff scrutinized since baseline `f70a857`:
`dna/brief.py`, `dna/builder.py`, `dna/embeddings.py` (commits 5ddefcf Batch J,
2f8c8ee Issue 82a AsyncAnthropic, 59dd2f0 typing ratchet, 6a38dbe Issue 109a split).
Code claims verified by reading (file:line); SDK claims verified against the installed
voyageai 0.3.2 in this environment.

## Resolved since 2026-07-01

- **[was SEV2] embeddings.py un-scoped tenacity retry — FIXED** (commit 5ddefcf,
  Issue 352 Batch J). dna/embeddings.py:40-44 now carries
  `retry=retry_if_exception_type(_TRANSIENT_VOYAGE_ERRORS)` scoped to
  `(RateLimitError, ServiceUnavailableError, Timeout, APIConnectionError)` — the exact
  transient set voyageai 0.3.2's own client retries, plus the network-drop class.
  Permanent auth/bad-request errors now surface on the first attempt. Verified all four
  names exist in `voyageai.error` for the installed 0.3.2. (No dedicated regression test
  asserting the predicate was added; acceptable under the 80/20 testing rule — the
  predicate is declarative config, not logic.)

## Findings

- [SEV2] dna/builder.py:87-96 — `_optimal_upload_gap_h` is a near-duplicate of
  `upload_intel/timing.py:optimal_gap_hours` (:74-99) but is missing BOTH fixes Batch J
  applied there: (1) the circular-week wrap — `min(gap, 168 - gap)` — so two peaks at
  e.g. Sunday 23:00 (slot 167) and Monday 01:00 (slot 25) yield a 142 h gap instead of
  the true 26 h shorter arc, skewing the brief's "upload rhythm" estimate and the
  `optimal_upload_gap_h` column persisted via `profile.create_draft`; and (2) the
  `_coerce_row` malformed-row guard, so a row with a bad `activity_index` raises inside
  the DNA build instead of being filtered. DRY violation + the exact defect class already
  fixed once elsewhere. | fix: delete `_optimal_upload_gap_h` and delegate —
  `from upload_intel.timing import optimal_gap_hours` and call
  `optimal_gap_hours(activity_rows)` at builder.py:325 (verified signature-compatible:
  `_coerce_row` reads `.day_of_week/.hour/.activity_index` attributes, exactly what the
  `AudienceActivity` ORM rows carry). Update `tests/test_dna.py:146-165` to import the
  shared function or drop in favour of upload_intel's tests.

- [cleanup] dna/brief.py:125-130 — (carry-forward from 2026-07-01) `generate_brief`
  docstring still says `stated_identity` "is injected as a system block BEFORE the
  volatile performance corpus and AFTER the static instructions". Stale since Issue 224
  moved it to the user turn via `wrap_untrusted` (actual code brief.py:96-102). This is
  the prompt-injection boundary for creator-authored text, so the drift is misleading on
  a security-relevant path. | fix: state it is passed in the user turn, JSON-wrapped via
  `wrap_untrusted`, never in the system role. (Implementation itself remains correct.)

- [cleanup] dna/embeddings.py:22-29 — (carry-forward from 2026-07-01) `_voyage()` lazy
  `global` singleton unguarded for first-call concurrency; two threads racing the first
  embed via `asyncio.to_thread` can each construct a `voyageai.Client`. Harmless (last
  writer wins, extra client GC'd). | fix: construct eagerly at module load (as
  brief.py:28 does for AsyncAnthropic) or wrap init in a `threading.Lock`. Low priority.

## Notes verified (not findings)

- **Issue 82a async migration (diff scrutiny).** brief.py now uses a module-level
  `AsyncAnthropic` singleton (:28-32, timeout + max_retries set) and `generate_brief`
  is `async def` with the `.create()` call properly awaited (:181). No sync/blocking
  call hidden in the async path; the verbose `vlog_llm_request/response` hooks are
  logging-only and env-gated (`VERBOSE_LOGGING`, prod requires explicit
  `VERBOSE_LOGGING_ALLOW_PROD` opt-in — verbose.py:4-13, recorded in DECISIONS
  2026-06-29), so full-prompt logging is not a default-path PII leak.
- **Issue 109a split (diff scrutiny).** `_enrich_videos` (builder.py:199-220) is now a
  thin stitch over three focused IN-query loaders (`_load_hook_texts`,
  `_load_signal_counts`, `_load_retention_rows`) — still 3 queries total regardless of
  video count, no N+1 reintroduced, all loaders keyed on `video_id`s already filtered to
  the creator by `rank_videos`, so no cross-tenant path.
- **Honest cold-start (Issue 109c).** The cold-start principle-citation change
  ("Pattern interrupt" instead of "Retention curve is ground truth") landed in
  `clip_engine/` — outside this slice. Within dna/, nothing cites a principle it doesn't
  implement: builder ranking is `engagement_rate × recency_weight` (90-day half-life,
  builder.py:28/35-42) — the creator's own signal with exponential recency decay
  (Principles 6/10/11), and the brief's system prompt phrases everything as likelihood
  estimates with the honesty disclaimer appended (brief.py:34-38, :56).
- **Prompt caching (rubric 5).** Still correctly OMITTED: the static instruction block
  is ~570–650 tokens, below the 1,024-token cacheable floor, so a `cache_control`
  marker would be inert (Issue 315, documented at brief.py:4-11). Token usage logged
  and `record_llm_metric` fired on both `.create` (:194-212) and streaming (:162-168)
  paths; `warn_if_truncated` on the non-stream path (:213) and inside
  `worker/anthropic_stream.py` for the stream path; `max_tokens=2000` on both.
- **Per-creator isolation (rubric 3).** Every creator-scoped query carries
  `creator_id`: builder.py `rank_videos` :119-123, insufficient-data counts :274/283,
  activity :322; identity.py get_current/get_history/upsert :30-33/:44/:81;
  profile.py create_draft/confirm_draft/get_active/get_version :53/:111/:168/:184;
  onboarding.py `_has_clip_track_videos` :103-106. No token/PII in any log line; no
  virality promise (disclaimer brief.py:34-38; prompt forbids it :56).
  Parameterized SQLAlchemy only.
- **Concurrency correctness.** identity.upsert_identity and profile.confirm_draft both
  still use `SELECT ... FOR UPDATE` + partial-unique-index backstop + IntegrityError
  recover-and-retry — idempotent and race-safe. Voyage SDK + tenacity sleeps offloaded
  via `asyncio.to_thread` (embeddings.py:53-58).
- **Bounded work (rubric 2).** rank_videos capped by `DNA_LONGS_CAP`/`DNA_SHORTS_CAP`;
  enrichment bounded to top+bottom; identity history capped at 20. No unbounded
  fetchall.
- **conflict.py / onboarding.py** unchanged since prior pass; conflict surfaces
  stated-vs-inferred mismatch as a UI nudge rather than silently overriding stated
  identity (COMPLIANCE honesty posture); onboarding resolver issues at most one
  follow-up query per call.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — Anthropic eager singleton, Voyage lazy singleton (cleanup noted); sessions caller-managed with explicit `commit` flag |
| 2 Concurrency & scale | ok — to_thread offload, bounded queries, IN-batched loaders (no N+1), FOR-UPDATE locks |
| 3 Security & compliance | ok — per-creator filter on every query; untrusted identity wrapped in user turn; no PII/token logs; no virality promise |
| 4 Clip-quality | 1 SEV2 — recency-decayed creator-own-data ranking sound, but upload-gap math missing circular-week wrap (builder.py:87) |
| 5 Anthropic SDK | ok — caching correctly omitted (sub-1024 floor); tokens logged both paths; truncation warned; max_tokens set |
| 6 Cleanliness & typing | 2 cleanup (carry-forward) — stale security-relevant docstring (brief.py:125), lazy-singleton race (embeddings.py:22); DRY component of the SEV2 also lives here |
| 7 Error handling / API | n/a (no router in slice) |
| 8 Config & paths | ok — all config via `settings`; no hardcoded paths/secrets |

## Module verdict
NEEDS-WORK (minor) — prior SEV2 (retry predicate) fixed; one new SEV2 (builder's
upload-gap duplicates upload_intel/timing.py but lacks its circular-week wrap +
malformed-row guard) plus two carried-forward cleanups; isolation, async, caching,
and concurrency all sound.
