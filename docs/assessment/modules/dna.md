# dna — assessed 2026-06-24

Slice: `dna/` (8 files: `__init__.py` [empty], `brief.py`, `builder.py`,
`conflict.py`, `embeddings.py`, `identity.py`, `onboarding.py`, `profile.py`).
This sweep re-verified the carried-over findings from the 2026-06-09 file
line-by-line against current code — all four prior SEV2s remain present and
unfixed — and adds two new SEV2s (inert prompt cache; upstream-derived video
isolation). Prior tracking-issue dispositions are noted per finding.

## Findings

- [SEV2] dna/builder.py:87-96 — `_optimal_upload_gap_h` builds gaps from
  `day_of_week*24 + hour`, sorts, and takes pairwise diffs but **never wraps the
  week boundary and never includes the last-to-first wrap gap**. Two activity
  peaks at Sat-23h and Sun-1h (Sun = day 0) produce a ~144h "gap" instead of
  ~2h, so the mean cadence that feeds the brief's "Optimal Clip Profile / upload
  rhythm" line is systematically biased. Still present (was tracked to Issue
  200, not landed). | fix: map peaks to absolute hour-of-week, sort, take
  pairwise gaps INCLUDING the wrap `(168 - h_last) + h_first`, then mean. Test:
  Sun-23h + Mon-1h peaks must average ~2h, not ~142h.
- [SEV2] dna/brief.py:91-96 — the `cache_control: ephemeral` breakpoint on the
  static system block is **inert**: the cacheable prefix
  (`UNTRUSTED_CONTENT_POLICY` + `_SYSTEM_INSTRUCTIONS`) measures ~445-580 tokens
  (verified: 2312 chars / 334 words), below Sonnet 4.x's 1024-token cacheable
  floor, so it never writes or reads a cache entry. The DNA-brief call is a
  recurring per-creator LLM call and prompt caching is *mandatory* per the
  architecture constraints, yet this call gets zero caching benefit. The
  docstring (brief.py:8-11) honestly documents the inertness, but the gap is
  unaddressed and not in DECISIONS as an accepted deviation. | fix: cross the
  1024-token floor so the marker activates — fold a larger stable rubric /
  few-shot exemplar block into `_SYSTEM_INSTRUCTIONS` ahead of the breakpoint,
  OR record the accepted deviation in docs/DECISIONS.md (architecture says
  caching is mandatory) rather than shipping a dead marker. (efficiency / cost
  — no correctness risk)
- [SEV2] dna/builder.py:35-42 — `_recency_weight(published_at=None)` returns a
  magic `0.5`, which equals e^(-λ·90) — i.e. a date-less row is silently scored
  as a ~90-day-old video. Under bad ingest data this can float a null-date video
  above genuinely-recent ones in `weighted_score` and re-order the top/bottom
  split that drives the brief. Still present (was tracked to Issue 76). | fix:
  exclude null-date rows at the SQL level in `rank_videos`
  (`Video.published_at.is_not(None)`), or return `0.0` + a counter log; record
  the choice in docs/DECISIONS.md.
- [SEV2] dna/builder.py:164-181 — `_enrich_videos` reads `Transcript`,
  `Signals`, and `RetentionCurve` filtered only by `video_id.in_(ids)` with no
  `creator_id` predicate. **Safe today** because `ids` come from `rank_videos`
  (builder.py:119, `Video.creator_id == creator_id`), so they are provably the
  requesting creator's — but the cross-tenant safety of these three reads lives
  entirely in an upstream filter two functions away. A future second caller
  passing externally-sourced ids would silently leak another creator's
  transcript/signals/retention. | fix: defense-in-depth — thread `creator_id`
  into `_enrich_videos`, join `Video`, and add `Video.creator_id == creator_id`
  to each of the three selects so isolation is query-local, not assumed. Not a
  present leak; promote to SEV1 only if `_enrich_videos` gains a second caller.
- [SEV2] dna/embeddings.py:67-69 + 117-118 — a missing `VOYAGE_API_KEY` is a
  WARNING-and-skip in `embed_patterns` and a **fully silent** no-op in
  `embed_brief` (no log at all). In production this silently leaves
  `dna_embeddings` empty and degrades downstream similarity ranking with no
  signal. Still present (was tracked to Issue 228). | fix: gate on
  `settings.ENV == "production"` (config.py:263) and raise
  `RuntimeError("VOYAGE_API_KEY required in production")`; keep the dev warning
  and add the missing log line to `embed_brief`.
- [SEV2] dna/conflict.py:34-42 — `_NICHE_KEYWORDS` hardcodes YouTube category
  ids ("27","26",…) inline with no import-time validation against
  `youtube.categories.NICHE_IDS` (categories.py:48, a `frozenset`). All 7 keys
  are valid today, but if a category id drifts out of `NICHE_OPTIONS` nothing
  trips — the detector silently stops matching that niche and false-positives a
  conflict nudge. Still present (was tracked to Issue 109). | fix: add a
  module-load assert `set(_NICHE_KEYWORDS) <= NICHE_IDS` so a renumber is a loud
  import error; keep the keyword table local.
- [cleanup] dna/builder.py:58 / 87 / 298 — `retention_rows: list`,
  `activity_rows: list`, and nested `_avg(vals: list)` use bare `list` where the
  element type is known. | fix: annotate `list[RetentionCurve]`,
  `list[AudienceActivity]`, and `list[float | None]` so the attribute/element
  access is type-checked.
- [cleanup] dna/builder.py:115 — nested `_base_query(kind, cap)` has no return
  annotation. | fix: annotate `-> Select[tuple[Video, VideoMetrics]]`.
- [cleanup] dna/profile.py:55 — `max_version = result.scalar() or 0` hides the
  no-rows case behind `or`. | fix: `result.scalar_one_or_none() or 0` — pure
  readability, behavior identical. (The same `... or 0` idiom also appears at
  identity.py:89 via `rows[0].version if rows else 0`, which is already
  explicit.)
- [cleanup] dna/brief.py:174-175 — `# type: ignore[arg-type]` on `system=` /
  `messages=` is a documented anthropic-0.40 stub limitation; runtime payload is
  correct. | fix: drop both ignores on the next SDK bump once `TextBlockParam`
  carries `cache_control` (tracked Issue 82).
- [cleanup] dna/embeddings.py:21-28 — `_VOYAGE` lazy singleton has no lock; two
  `asyncio.to_thread` workers racing the first call can both construct a client
  (benign — one is GC'd, no pooled resource held). Laziness is intentional (key
  may be empty in dev). | fix: guard with a `threading.Lock`, or accept the
  benign race with a one-line comment.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — sessions are caller-owned `AsyncSession` (passed in, never opened here); `commit=False` opt-out lets the worker land draft+embeddings atomically (profile.py:33, embeddings.py:53). Anthropic client is a module singleton with explicit timeout+retries (brief.py:27-31); Voyage client is a lazy module singleton (embeddings.py:21-28, 1 benign-race cleanup). No temp media / subprocess / file handles in this slice. |
| 2 Concurrency & scale | ok — no sync/blocking call inside any `async def` (Voyage sync SDK + tenacity sleeps correctly offloaded via `asyncio.to_thread`, embeddings.py:40-45). Reads bounded: `rank_videos` capped at DNA_LONGS_CAP(50)+DNA_SHORTS_CAP(75); `_enrich_videos` batched into 3 IN-queries (no N+1, builder.py:164-183); `get_history` capped at limit=20; `resolve_setup_step` issues ≤1 indexed follow-up. `upsert_identity`/`confirm_draft` use `with_for_update()` row locks released on commit/rollback on every path. |
| 3 Security & compliance | 1 SEV2 — `_enrich_videos` (builder.py:164) isolation is upstream-derived, not query-local (defense-in-depth gap, not a present leak). All other creator-scoped reads filter by `creator_id` (verified: profile.py 53/111/167/183; identity.py 30/42/81; onboarding.py 102 + `check_data_gate`; builder.py 119/255/261/303; embedding rows inserted with explicit `creator_id`). Parameterized SQLAlchemy throughout — no f-string SQL. No tokens/PII in any `logger.*` line (creator_id UUID + version/count ints only). DNA is creator-owned derivative data; no OAuth-token handling in this slice. Honesty disclaimer appended on both brief paths (brief.py:169,199) — no virality promise anywhere. |
| 4 Clip-quality | partial — recency decay is real and exponential: `_recency_weight = e^(-λ·age_days)`, λ=ln(2)/90 (90-day half-life), in `weighted_score` (builder.py:28,35-42,148). Ranking is against THIS creator's own engagement×recency, never a generic score (Principle 11); `optimal_clip_len_s` feeds Principle 10 (native length), `best_source_region` feeds Principle 6 (retention is ground truth). conflict.py is the explicit don't-silently-override-stated-intent guardrail; brief.py:57-64 instructs surfacing stated-vs-inferred disagreement. No inline principle citation here — that obligation lives in clip_engine scoring, not the descriptive brief. The `_optimal_upload_gap_h` week-wrap SEV2 biases the cadence recommendation; the `0.5` dateless weight SEV2 can perturb the top/bottom split. |
| 5 Anthropic SDK | 1 SEV2 — the `cache_control` marker is present (brief.py:91) but **inert**: the static prefix (~445-580 tokens) is below the 1024-token cacheable floor, so mandatory caching saves nothing on a recurring per-creator call. Token usage IS logged after every call on both paths (non-streaming brief.py:180-186; streaming brief.py:162-168, sourced from the shared `worker.anthropic_stream` helper). `max_tokens=2000` on both. Untrusted creator-stated identity correctly kept OUT of the system role and JSON-wrapped via `wrap_untrusted` into the user turn (brief.py:104-107) — matches OWASP LLM01 guidance. No web-search tool (none intended for DNA-brief synthesis over already-fetched data). |
| 6 Cleanliness & typing | cleanups only (bare-`list` element types, a missing nested return annotation, a `... or 0` idiom, a documented type-ignore pair, the lazy-singleton race). No `print()`, no TODO/FIXME, no commented-out code, no debug statements anywhere in the slice. All public entry points typed, including the onboarding.py `TypedDict`/`Literal` surface. |
| 7 Error handling / API | n/a — no FastAPI router in this slice. Validation helpers (identity.py:185-236) raise `ValueError` for the router's Pydantic layer to translate; `build_patterns` raises an operator-actionable `ValueError` with bucket counts plus a structured `dna_build_insufficient_data` log event (builder.py:268-282). Write-boundary handling is sound: `IntegrityError` rollback-and-refetch on the partial-unique race in both `upsert_identity` (identity.py:118-132) and `confirm_draft` (profile.py:138-157). |
| 8 Config & paths | ok — all knobs via `pydantic-settings` `settings.*` (ANTHROPIC_API_KEY/MODEL, VOYAGE_API_KEY, MIN_VIDEOS_FOR_DNA, MIN_SHORTS_FOR_DNA, DNA_LONGS_CAP, DNA_SHORTS_CAP — all in config.py with defaults). Empty `VOYAGE_API_KEY` degrades gracefully in dev (the prod fail-fast gap is the SEV2 above). No filesystem paths in this slice. |

## Module verdict
NEEDS-WORK — no BLOCKER (every creator-scoped read is filtered, nothing
sensitive is logged, no virality promise, all async paths non-blocking), but
six SEV2s persist: the upload-gap week-wrap bug, the `0.5` dateless-video
weight, the prod-silent `VOYAGE_API_KEY` skip, and the unvalidated hardcoded
niche-id table all carry over unfixed from the prior sweep, plus a newly-flagged
inert prompt cache (mandatory caching saves nothing on a recurring per-creator
call) and a `_enrich_videos` isolation that is upstream-derived rather than
query-local.
