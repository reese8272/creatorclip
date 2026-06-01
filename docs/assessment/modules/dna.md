# dna — assessed 2026-05-31 (Wave-9 re-verify)

## Findings

- [SEV2] dna/brief.py:21-25 — `_ANTHROPIC: Anthropic = Anthropic(api_key=settings.ANTHROPIC_API_KEY, …)`
  is still constructed at **import time**. Wave-9 Issue 108 added the explicit
  type annotation (cosmetic) but did NOT convert to a lazy singleton, so the
  underlying defect persists: if `ANTHROPIC_API_KEY` is missing or invalid the
  entire module fails to import, which cascades to `worker/tasks.py` (DNA
  pipeline), `routers/clips.py`, and `routers/creators.py` — an unrelated
  config gap turns into a worker boot failure rather than a per-call error.
  Tracked as Issue 109 follow-up (deferred design-work) | fix: convert to a
  lazy singleton with the same shape as `dna/embeddings.py:24-28`
  (`_voyage()`) — `_ANTHROPIC: Anthropic | None = None` then
  `def _client() -> Anthropic: global _ANTHROPIC; if _ANTHROPIC is None:
  _ANTHROPIC = Anthropic(...); return _ANTHROPIC`. Same module-level singleton
  lifetime, but constructed on first call so import is side-effect-free.

- [SEV2] dna/conflict.py:34-42 — `_NICHE_KEYWORDS` still only covers 7 of the
  YouTube niche ids (27, 26, 20, 23, 10, 17, 28). The early-return at lines
  83-86 treats *any* unmapped niche as already-matched, silently disabling
  the conflict detector for the majority of creators — defeating the Issue 83
  "surface, don't silently override" principle that this whole module exists
  to enforce. Explicitly listed as deferred item #10 in Issue 109 ("the
  keyword coverage gap itself is a separate concern") | fix: either (a)
  populate `_NICHE_KEYWORDS` for every id in `youtube.categories.NICHE_IDS`
  (one row per supported niche; pull keywords from the existing niche label
  text), or (b) flip the unmapped-niche policy to "no opinion" (skip it from
  the matched set) so a single mapped niche with zero matches still surfaces
  a nudge. Whichever path, add a unit test that iterates `NICHE_IDS` and
  asserts every id either has keywords or is explicitly listed as opted-out,
  so adding a new niche to `youtube/categories.py` forces a conscious
  decision here.

- [SEV2] dna/embeddings.py:31 — the `@retry` decorator from tenacity still
  defaults to `reraise=False` (no `reraise=True` argument added in Wave-9),
  so the final failure wraps the underlying exception in
  `tenacity.RetryError`. Callers in `worker/tasks.py` catching broad
  `Exception` will see this, but anything narrower (e.g. catching the
  Voyage `APIError` to credit-refund the creator) will miss it. NOT
  addressed by Issue 108 (which only added the `-> Any` return type) and
  NOT in the Issue 109 deferred list — appears to have fallen through the
  cracks | fix: add `reraise=True` so the original
  `voyageai.error.APIError` / network error propagates verbatim. Matches
  how `worker/anthropic_stream.py` and other upstream retry sites behave.

- [cleanup] dna/builder.py:148-196 — `_enrich_videos` is still doing four
  jobs (transcript hook extraction, signals counts, retention map build,
  region derivation) in one ~50-line function (KISS / single-responsibility).
  The three IN-queries are correctly batched (good), but the per-video loop
  at 179-195 has grown to the point where a reader has to mentally re-derive
  which side-effects land on which dict keys. Explicitly listed as deferred
  item #1 in Issue 109 | fix: split into
  `_load_transcript_hooks(session, ids) -> dict[uuid.UUID, str]`,
  `_load_signal_counts(session, ids) -> dict[uuid.UUID, tuple[int, int]]`,
  `_load_retention(session, ids) -> dict[uuid.UUID, list[RetentionCurve]]`,
  then a thin loop that just stitches them onto the dicts. Same query
  shape, smaller cognitive load, each loader independently testable.

- [cleanup] dna/builder.py:198-215 — `_video_summary` is a static field-map
  whose key list duplicates the field set already built by `_enrich_videos`.
  Adding a new derived field requires a touch in two places (DRY) | fix:
  either define a single `TypedDict` for the summary shape and project from
  it, or accept the duplication and add a comment in each function pointing
  at the other so future maintainers know they are paired. Naturally falls
  out of the Issue 109 #1 split if that lands.

- [cleanup] dna/brief.py:60-98 — `_build_request` declares its return as
  `tuple[list[dict], list[dict]]` but the dicts are `dict[str, Any]` with
  heterogeneous values (strings, nested dicts, the `cache_control` block
  added at line 88). The looser annotation is what forces the
  `# type: ignore[arg-type]` at lines 156-157 | fix: import the Anthropic
  SDK's `MessageParam` / `TextBlockParam` types (or define local
  `TypedDict`s if the SDK stubs still predate `cache_control`) so the
  return type is precise and the call site loses the `# type: ignore`.

- [cleanup] dna/embeddings.py:32, 40 — Wave-9 Issue 108 added `-> Any` to
  `_embed` and `_aembed`, which satisfies the "every signature typed"
  mandate mechanically. The inline comment correctly acknowledges this is
  intentional looseness ("typed loosely as Any here so a Voyage SDK rename
  surfaces at the call site"). This is a defensible tradeoff but worth
  re-visiting once `voyageai` ships proper stubs — at that point the
  concrete return type (e.g. `voyageai.api.EmbeddingsObject`) gives the
  call-site `.embeddings` access a static check without losing the
  rename-detection property | fix: re-evaluate on next `voyageai` upgrade;
  no action required today.

## Wave-9 verification (Issue 108 cleanup sweep)

The three cleanup items the prompt flagged for re-verify are confirmed
resolved:

- **dna/brief.py:21** — `_ANTHROPIC: Anthropic = Anthropic(...)` now carries
  an explicit type annotation. Note: the deeper SEV2 (import-time
  construction) is unchanged — only the annotation cleanup landed.
- **dna/embeddings.py:32, 40** — `_embed` and `_aembed` now both return
  `-> Any`, with an inline comment documenting why `Any` is the
  deliberate choice over a concrete Voyage SDK type.
- **dna/identity.py** — `_ = sa` and the `import sqlalchemy as sa` line
  itself are both removed. No remaining references to the `sa` alias.

Of the three pre-existing SEV2s the prompt asked about:

- **brief.py:21 Anthropic-at-import-time** — still open; explicitly
  deferred to Issue 109 (not in the listed 10 items but in the same
  design-work-required spirit). Flagged above.
- **conflict.py:34 keyword coverage gap** — still open; explicitly listed
  as Issue 109 deferred item #10. Flagged above.
- **embeddings.py tenacity `reraise=False` default** — still open AND
  appears to have fallen through the cracks: not in the Issue 109 list,
  not addressed by Issue 108. Flagged above as a true carry-forward.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — Voyage client is a lazy module-level singleton; Anthropic client is an eager module-level singleton (see SEV2 above for the eager-vs-lazy concern). Sessions are passed in by the caller (worker/router) and not opened inside the module. No file handles, subprocesses, or temp media. |
| 2 Concurrency & scale | ok — `_aembed` correctly wraps the sync Voyage SDK in `asyncio.to_thread` (Issue 38 W1 + Issue 68 fixes intact). `rank_videos` bounded by `settings.DNA_MAX_CANDIDATE_VIDEOS=500` (Issue B). `_enrich_videos` batched into 3 IN-queries (no N+1). `identity.upsert_identity` and `profile.confirm_draft` both use `with_for_update()` plus a partial unique index — correctly handle the concurrent-write race. |
| 3 Security & compliance | ok — every query touching a creator-scoped table filters by `creator_id` (builder.py:119, 249, 257, 297; profile.py:53, 83, 109-117, 146-149, 167-172, 184-187; identity.py:31, 44, 80-86, 96-99). All SQL is SQLAlchemy ORM (parameterized). Logs include `creator_id` UUID only (internal id, not PII), brief token counts, DNA version numbers — no tokens, no email, no channel identity. The brief's honesty disclaimer is appended to every output (brief.py:27-31, 151, 173) — no virality promise anywhere. |
| 4 Clip-quality | ok — Principle 11 ("Audience-fit over generic virality") structurally enforced: DNA built from *this* creator's recency-weighted engagement (builder.py:_recency_weight), patterns inferred from *this* creator's top/bottom videos (builder.py:284-288), brief generated against *this* creator's stated identity + inferred patterns (brief.py:35-57). Principle 10 ("Native length over generic length") at builder.py:75-84 (`_optimal_clip_len_s`) and `optimal_upload_gap_h`. Principle 6 ("Retention curve is ground truth") underpins `_best_source_region` (builder.py:58-72). Conflict detector (conflict.py) operationalizes the "surface stated-vs-inferred disagreement" principle from Issue 83 — but see SEV2 above for the coverage gap that weakens it in practice. No clip-start/setup logic in this module — that is `clip_engine/`'s job. |
| 5 Anthropic SDK | mostly ok — prompt caching attempted with correct structure (stable instructions first, optional stable identity second, volatile corpus last; `cache_control: ephemeral` on the last stable block — brief.py:82-90). Token usage logged after every call including cache_read / cache_creation (brief.py:144-150, 160-166). `max_tokens=2000` set. Module docstring (brief.py:1-9) honestly flags the static prefix is below the 2048-token minimum cacheable size for Sonnet 4.6 — already in `docs/DECISIONS.md`. No web-search tool here, which is correct: this call is synthesis from in-DB data. |
| 6 Cleanliness & typing | mostly ok — cleanup-severity typing/DRY items above. No `print()`, no commented-out blocks, no `TODO`. Wave-9 closed the explicit-typing gaps on `_ANTHROPIC`, `_embed`, `_aembed`. Remaining typing looseness is in the `dict` returns of `dna/brief.py:60` (forcing two `# type: ignore` lines). `_enrich_videos` remains the only function over 30 lines doing more than one thing (Issue 109 deferred). |
| 7 Error handling / API | n/a — no router endpoints in this module. The module raises `ValueError` at well-defined boundaries (builder.py:272 insufficient data, profile.py:125 no draft, identity validators) which routers translate to HTTP. `RuntimeError` at brief.py:170 if Claude returns no text block — caught by worker. Identity `IntegrityError` paths (identity.py:120, profile.py:140) correctly roll back and either return the winning concurrent row or re-raise. |
| 8 Config & paths | ok — all referenced settings exist in `config.py` (`MIN_VIDEOS_FOR_DNA=10`, `MIN_SHORTS_FOR_DNA=5`, `DNA_MAX_CANDIDATE_VIDEOS=500`, `ANTHROPIC_MODEL`, `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`) and the three DNA-tunable ones are present in `.env.example` with descriptions. No filesystem paths in this module. |

## Module verdict

**NEEDS-WORK** — no BLOCKERs and per-creator isolation is correctly enforced
on every query. Wave-9 Issue 108 closed the three explicit cleanup items the
prompt asked about (`_ANTHROPIC` annotation, `_embed`/`_aembed` return types,
dead `_ = sa` alias). Three SEV2s remain: import-time Anthropic construction
(Issue 109 design-deferred), `_NICHE_KEYWORDS` coverage gap (Issue 109
deferred item #10), and tenacity `reraise=False` default (carry-forward that
appears to have fallen through the cracks — not in the Issue 109 list and
not addressed by Issue 108). None are scale-killers on their own, but the
tenacity one is the cheapest fix and would close the carry-forward chain.
