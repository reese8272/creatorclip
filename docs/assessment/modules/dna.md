# dna — assessed 2026-06-09

## Findings

- [SEV2] dna/builder.py:87-96 — `_optimal_upload_gap_h` computes gaps from
  `day_of_week*24 + hour` but never wraps the week boundary. Top peaks spanning
  Sat-night → Sun-morning produce a ~144h "gap" instead of ~2h (Sun is day 0),
  and the last-to-first wrap gap is never included, so the mean cadence is
  systematically biased — this feeds the "Optimal Clip Profile / upload rhythm"
  section of the creator brief. Unchanged since 2026-06-07.
  | fix: convert peaks to absolute hour-of-week, sort, take pairwise gaps
  INCLUDING the wrap (`(168 - h_last) + h_first`); mean of all gaps. Test:
  Sun-23h + Mon-1h peaks must average 2h, not 142h.
- [SEV2] dna/builder.py:38-39 — `_recency_weight(published_at=None)` returns a
  magic `0.5`, treating a date-less row as a ~90-day-old video. Under bad
  ingest data this inflates date-less videos into the top bucket and re-orders
  top/bottom splits. Unchanged since 2026-06-07. | fix: filter at the SQL level
  in `rank_videos` via `Video.published_at.is_not(None)` (or return `0.0` and
  log a counter); record the choice in docs/DECISIONS.md.
- [SEV2] dna/conflict.py:34-42 — `_NICHE_KEYWORDS` hardcodes YouTube category
  ids ("27", "26", …) inline instead of deriving from
  `youtube/categories.py::NICHE_IDS` (categories.py:48). Verified today: all 7
  hardcoded keys are currently valid, but nothing trips at import if a key
  drifts out of `NICHE_OPTIONS` — the detector would silently stop matching
  that niche and false-positive a conflict nudge. | fix: add a module-load
  assert `set(_NICHE_KEYWORDS) <= NICHE_IDS` so a rename/renumber is a loud
  import error; keep the keyword table local.
- [SEV2] dna/embeddings.py:67-69 + 117-118 — missing `VOYAGE_API_KEY` is a
  WARNING-and-skip in `embed_patterns` and a fully silent no-op in
  `embed_brief` (no log at all). In production this leaves `dna_embeddings`
  empty and downstream similarity ranking silently degraded. | fix: gate on
  `settings.ENV == "production"` (config.py:152) and raise
  `RuntimeError("VOYAGE_API_KEY required in production")`; keep the dev
  warning, and add the missing log line to `embed_brief`.
- [cleanup] dna/embeddings.py:21-28 — `_VOYAGE` lazy singleton has no lock;
  two `asyncio.to_thread` workers racing the first call can both construct a
  client (benign — one is leaked, no pooled resource held). Laziness is
  intentional (key may legitimately be empty in dev), so eager construction à
  la `_ANTHROPIC` is not a drop-in. | fix: guard with a `threading.Lock`, or
  accept the benign race with a one-line comment; pairs with the prod
  fail-fast finding above.
- [cleanup] dna/brief.py:156-157 — `# type: ignore[arg-type]` on `system=` /
  `messages=` is a documented anthropic-0.40 stub limitation (see brief.py:85-88).
  Runtime payload is correct. | fix: re-evaluate on the next anthropic version
  bump (tracked via docs/issues.md); drop both ignores once `TextBlockParam`
  carries `cache_control`.
- [cleanup] dna/builder.py:58 — `retention_rows: list` lacks element type
  | fix: annotate `list[RetentionCurve]`.
- [cleanup] dna/builder.py:87 — `activity_rows: list` lacks element type
  | fix: annotate `list[AudienceActivity]`.
- [cleanup] dna/builder.py:115 — nested `_base_query(kind, cap)` missing a
  return annotation | fix: annotate `-> Select[tuple[Video, VideoMetrics]]`.
- [cleanup] dna/builder.py:298 — nested `_avg(vals: list)` is a bare `list`
  | fix: annotate `vals: list[float | None]`.
- [cleanup] dna/profile.py:55 — `max_version = result.scalar() or 0` hides the
  no-rows case behind `or` | fix: `result.scalar_one_or_none() or 0`. Pure
  readability; behavior identical.

All five carried-over SEV2/cleanup claims from the 2026-06-08 file were
re-verified line-by-line against current code today — none were fixed.
`dna/onboarding.py` (new since the last sweep) was reviewed in full: both
queries are creator-scoped (onboarding.py:92-95), the `active`-path
`COUNT(*)` rides the `(creator_id, youtube_video_id)` unique index
(models.py:251), all functions are typed, and no findings were raised.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — sessions are caller-provided with a `commit=False` opt-out so the worker commits DNA + embeddings atomically (profile.py:33, embeddings.py:53); module-level Anthropic singleton with explicit timeout + retries (brief.py:21-25); lazy Voyage singleton (1 cleanup). |
| 2 Concurrency & scale | ok — sync Voyage SDK + tenacity sleeps offloaded via `asyncio.to_thread` (embeddings.py:40-45); ranking bounded by `DNA_LONGS_CAP`/`DNA_SHORTS_CAP` (builder.py:128-131); `_enrich_videos` collapses the old 3×N round-trips into 3 IN-queries (builder.py:154-183); `resolve_setup_step` issues at most one indexed follow-up query per call; no blocking call inside any `async def`. |
| 3 Security & compliance | ok — every creator-scoped query carries `WHERE creator_id` (builder.py:120/255/263/303; identity.py:31/44/81; profile.py:53/111/146/170/185; onboarding.py:93; embeddings rows are inserted with explicit `creator_id`). Video-scoped joins (Transcript/Signals/RetentionCurve) traverse ids already creator-filtered in `rank_videos`. No token handling in slice; no PII/secret in any `logger.*` line (creator UUIDs only); honesty disclaimer appended on both brief paths (brief.py:27-31, 151, 173) — no virality promise anywhere. |
| 4 Clip-quality | partial — DNA is load-bearing for Principle #11 (Audience-fit over generic virality): ranking is recency-decayed engagement against THIS creator's own metrics (builder.py:35-42, 148), and `optimal_clip_len_s`/`best_source_region` feed Principle #10 (Native length). conflict.py is the explicit don't-silently-override-stated-intent guardrail; brief.py:50-57 instructs surfacing stated-vs-inferred disagreement. No inline principle citation here — that obligation lives in clip_engine scoring, not the descriptive brief. The `_optimal_upload_gap_h` SEV2 does bias the cadence recommendation. |
| 5 Anthropic SDK | ok — `cache_control: ephemeral` on the last stable system block, stable-first ordering (brief.py:76-90); token usage incl. cache read/write logged after every call on both paths (brief.py:144-150, 160-166); `max_tokens=2000`; streaming delegates to the shared `worker.anthropic_stream` helper so both paths share one cache prefix. Sub-2048-token static prefix (cache rarely engages) is honestly documented and DECISIONS-tracked. |
| 6 Cleanliness & typing | 7 cleanups (4 nested/helper annotations, 1 documented type-ignore pair, 1 scalar idiom, 1 lazy-singleton lock). No TODO, no `print()`, no commented-out code; all public entry points fully typed, including the new onboarding.py TypedDict surface. |
| 7 Error handling / API | n/a — internal module, no HTTP surface. Write-boundary handling is sound: `IntegrityError` rollback-and-refetch on the partial-unique race in identity.upsert_identity:118-132 and profile.confirm_draft:139-157; `build_patterns` raises an operator-actionable `ValueError` with bucket counts plus a structured `dna_build_insufficient_data` log event (builder.py:268-282). |
| 8 Config & paths | ok — all knobs from `config.settings` (ANTHROPIC_API_KEY/MODEL, VOYAGE_API_KEY, MIN_VIDEOS_FOR_DNA, MIN_SHORTS_FOR_DNA, DNA_LONGS_CAP, DNA_SHORTS_CAP); no filesystem paths in slice. The prod fail-fast gap for VOYAGE_API_KEY is the SEV2 above. |

## Module verdict

NEEDS-WORK — 4 SEV2 correctness findings (upload-gap week-wrap bug, dateless-video 0.5 weight, unvalidated hardcoded niche-id table, prod-silent VOYAGE_API_KEY skip) persist unchanged from the 2026-06-08 sweep; no BLOCKER — per-creator isolation holds on every query, nothing sensitive is logged, no virality promise, and the new onboarding.py resolver is clean.
