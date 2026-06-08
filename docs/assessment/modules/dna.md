# dna — assessed 2026-06-07

## Findings

- [SEV2] builder.py:88-96 — `_optimal_upload_gap_h` computes gaps from
  `day_of_week*24 + hour` but never wraps the week boundary. Top peaks spanning
  Sat-night → Sun-morning produce a 23h "gap" computed as `~144h` (Sun is day 0),
  and the last-to-first peak gap is not included at all, so the mean is
  systematically biased and can land outside any realistic upload cadence.
  | fix: convert each peak to absolute hour-of-week `h = day*24 + hour`, sort,
  then take pairwise gaps INCLUDING the wrap (`(168 - h_last) + h_first`).
  Mean of all three gaps with wrap is the unbiased cadence. Add a 2-row test:
  Sun-23h + Mon-1h should yield a 2h average, not 142h.
- [SEV2] builder.py:35-42 — `_recency_weight(published_at=None) -> 0.5` is a magic
  half-life default that silently inflates the weighted score of videos with
  missing publish dates (a 0.5 weight ≈ a 90-day-old video, treating a date-less
  row as "recently published"). Under bad ingest data this re-orders top/bottom
  buckets. | fix: either return `0.0` (and log a counter) so dateless rows fall
  out of the ranking, or filter dateless videos in `rank_videos` at the SQL
  level via `Video.published_at.is_not(None)`. Document the choice in DECISIONS.
- [SEV2] dna/conflict.py:34-42 — `_NICHE_KEYWORDS` hardcodes YouTube category IDs
  ("27", "26", …) inline instead of importing the canonical mapping from
  `youtube/categories.py`. If a category id is ever renumbered or removed
  upstream the conflict detector silently shifts to the wrong keyword set
  (e.g. flagging an education creator against gaming keywords). | fix: import
  the canonical NICHE_IDS / label_for and key `_NICHE_KEYWORDS` off the same
  symbolic ids the validator uses; add an assert at module load that every
  key in `_NICHE_KEYWORDS` is in `NICHE_IDS` so a rename trips at import.
- [SEV2] dna/embeddings.py:67-69 — `embed_patterns` logs `WARNING` and returns
  when `VOYAGE_API_KEY` is missing in production, leaving the DnaEmbedding
  table empty and downstream similarity queries (preference + clip_engine)
  silently degraded. | fix: in dev keep the warning, but in production fail
  fast — raise `RuntimeError("VOYAGE_API_KEY required")` when
  `settings.ENVIRONMENT == "production"`. Mirror the same gate in
  `embed_brief` (line 117) which currently no-ops with no log at all.
- [SEV2] dna/embeddings.py:21-28 — `_VOYAGE` lazy singleton is initialised
  without a lock; two `asyncio.to_thread` workers racing on first call can both
  construct a client (harmless leak), but more importantly the singleton is
  never invalidated, so a key rotation requires a process restart. | fix:
  construct the client eagerly at module import (same shape as `_ANTHROPIC`
  in brief.py:21) so it participates in normal singleton semantics; the
  warning-on-missing path becomes a startup failure instead of a per-call
  surprise. (Lower priority — paired with the previous finding.)
- [cleanup] dna/brief.py:156-157 — `# type: ignore[arg-type]` suppressing the
  `system=` / `messages=` arg mismatch is a documented Anthropic SDK 0.40 stub
  limitation (see lines 85-87). Re-evaluate next SDK bump; if the cache_control
  field has landed in `TextBlockParam`, drop both ignores. | fix: add a
  TODO-free reminder in `docs/issues.md` keyed to the next anthropic version
  bump, then delete the ignores when the stub catches up. Not a SEV — the
  runtime payload is correct; only the type-check is suppressed.
- [cleanup] dna/builder.py:58 — `retention_rows: list` lacks element type
  | fix: annotate `list[RetentionCurve]`.
- [cleanup] dna/builder.py:87 — `activity_rows: list` lacks element type
  | fix: annotate `list[AudienceActivity]`.
- [cleanup] dna/builder.py:115 — nested `_base_query(kind, cap)` is missing a
  return annotation | fix: annotate `-> Select[tuple[Video, VideoMetrics]]`.
- [cleanup] dna/builder.py:298 — nested `_avg(vals)` parameter is bare `list`
  | fix: annotate `vals: list[float | None]`.
- [cleanup] dna/profile.py:55 — `max_version = result.scalar() or 0` masks
  the legitimate `0` version case (no rows). Today `version` starts at 1 so
  `or 0` is safe, but the idiom hides intent | fix:
  `max_version = result.scalar_one_or_none() or 0`. Pure readability.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — sessions are caller-provided and respect a `commit=False` opt-out (profile.py:31, embeddings.py:65) so the worker can atomically commit DNA + embeddings together; module-level Anthropic singleton with explicit timeout (brief.py:21-25). |
| 2 Concurrency & scale | ok — sync Voyage SDK is wrapped via `asyncio.to_thread` (embeddings.py:45); ranking is bounded by `DNA_LONGS_CAP` / `DNA_SHORTS_CAP`; `_enrich_videos` collapses 3×N round-trips into 3 IN-queries; no blocking call inside an `async def`. |
| 3 Security & compliance | ok — every creator-scoped query carries an explicit `WHERE creator_id = …` (builder.py:120 / 255 / 263 / 303; identity.py:31, 44, 81; profile.py:53, 110, 169, 184; embeddings.py:88, 124). Video-scoped joins on `Transcript` / `Signals` / `RetentionCurve` traverse video ids that were already filtered by `creator_id` in `rank_videos`. Token handling is out of slice; no PII or token in any `logger.*` line; brief.py:27-31 appends the honesty disclaimer on both streaming and non-streaming paths. |
| 4 Clip-quality | partial — DNA outputs feed the clip engine, so this module is load-bearing for "Audience-fit over generic virality" (Principle #11). The brief structure is identity-aware (brief.py:35-57 surfaces stated-vs-inferred disagreement rather than overriding) and conflict.py is the explicit "don't silently re-rank by behaviour" guardrail. No principle is cited inline because the brief is descriptive, not a clip score — that obligation lives in `clip_engine/`. SEV2 in `_optimal_upload_gap_h` does affect the "Native length / cadence" recommendation. |
| 5 Anthropic SDK | ok — `cache_control: ephemeral` on the last stable block (brief.py:88); token usage logged after every call including cache hits/misses (brief.py:144-150, 160-166); `max_tokens=2000` set; streaming path delegates to the shared `worker.anthropic_stream` helper. The docstring honestly notes the static prefix is below the 2048-token minimum cacheable prefix so the cache rarely engages — fine, that's a DECISIONS-tracked tradeoff. |
| 6 Cleanliness & typing | 1 cleanup typing batch (4 nested-function annotations) plus the documented type-ignore in brief.py. No TODO, no `print()`, no commented-out blocks. All public entry points are fully typed. |
| 7 Error handling / API | n/a — internal module, no HTTP surface. Exception handling at write boundaries is sound: `IntegrityError` recovery on the partial-unique race in `identity.upsert_identity` (lines 118-132) and `profile.confirm_draft` (138-157) both follow the rollback-and-refetch pattern. `ValueError` from `build_patterns` (builder.py:278) carries operator-actionable counts. |
| 8 Config & paths | ok — every constant is sourced from `config.settings` (ANTHROPIC_API_KEY, VOYAGE_API_KEY, ANTHROPIC_MODEL, MIN_VIDEOS_FOR_DNA, MIN_SHORTS_FOR_DNA, DNA_LONGS_CAP, DNA_SHORTS_CAP). No filesystem paths used. |

## Module verdict

NEEDS-WORK — 4 SEV2 correctness findings (upload-gap wrap bug, dateless-video weight, hardcoded niche-id table drift, prod-silent VOYAGE_API_KEY fallback) plus minor typing cleanup. No BLOCKER: per-creator isolation is enforced on every query, no PII is logged, no virality promise is emitted, and the resource/concurrency posture is sound. The most behaviourally-impactful item is the `_optimal_upload_gap_h` week-wrap bug because it silently biases the cadence recommendation the brief surfaces to creators.
