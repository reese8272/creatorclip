# dna — assessed 2026-05-31

## Findings

- [SEV2] dna/brief.py:21-25 — `_ANTHROPIC = Anthropic(api_key=settings.ANTHROPIC_API_KEY, …)`
  is constructed at **import time**. If `ANTHROPIC_API_KEY` is missing or invalid
  the entire module fails to import, which cascades to `worker/tasks.py` (DNA
  pipeline), `routers/clips.py`, and `routers/creators.py` — i.e. an unrelated
  config gap turns into a worker boot failure rather than a per-call error |
  fix: convert to a lazy singleton with the same shape as `dna/embeddings.py:23`
  (`_voyage()`) — `def _client() -> Anthropic: global _ANTHROPIC; if _ANTHROPIC
  is None: _ANTHROPIC = Anthropic(...); return _ANTHROPIC`. Same module-level
  singleton lifetime, but constructed on first call so import is side-effect-free.

- [SEV2] dna/conflict.py:34-42 — `_NICHE_KEYWORDS` only covers 7 of the
  YouTube niche ids (27, 26, 20, 23, 10, 17, 28). The early-return at lines 83-86
  treats *any* unmapped niche as already-matched, which silently disables the
  conflict detector for the majority of creators — defeating the Issue 83
  "surface, don't silently override" principle that this whole module exists to
  enforce | fix: either (a) populate `_NICHE_KEYWORDS` for every id in
  `youtube.categories.NICHE_IDS` (one row per supported niche; pull keywords
  from the existing niche label text), or (b) flip the unmapped-niche policy
  to "no opinion" (skip it from the matched set) so a single mapped niche with
  zero matches still surfaces a nudge. Whichever path, add a unit test that
  iterates `NICHE_IDS` and asserts every id either has keywords or is
  explicitly listed as opted-out, so adding a new niche to `youtube/categories.py`
  forces a conscious decision here.

- [SEV2] dna/embeddings.py:30-40 — `_embed` and `_aembed` have no return type
  annotations (CLAUDE.md mandates type hints on every signature). The Voyage
  SDK's `embed()` returns an object whose `.embeddings` list the callers
  depend on at lines 81 and 117 — silently dropping the type means a future
  SDK rename would be a runtime AttributeError instead of a mypy error |
  fix: annotate with the concrete Voyage return type
  (e.g. `voyageai.api.EmbeddingsObject`, version-checked against
  `voyageai.__version__`) on both helpers so the call-site `.embeddings`
  attribute access is statically verified.

- [SEV2] dna/embeddings.py:30 — the `@retry` decorator from tenacity defaults
  to `reraise=False`, which means the final failure wraps the underlying
  exception in `tenacity.RetryError`. Callers in `worker/tasks.py` catching
  broad `Exception` will see this, but anything narrower (e.g. catching the
  Voyage `APIError` to credit-refund the creator) will miss it | fix: add
  `reraise=True` so the original `voyageai.error.APIError` / network error
  propagates verbatim. Matches how `worker/anthropic_stream.py` and other
  upstream retry sites behave.

- [cleanup] dna/builder.py:148-196 — `_enrich_videos` is doing four jobs
  (transcript hook extraction, signals counts, retention map build, region
  derivation) in one ~50-line function (KISS / single-responsibility). The
  three IN-queries are correctly batched (good), but the per-video loop at
  179-195 has grown to the point where a reader has to mentally re-derive
  which side-effects land on which dict keys | fix: split into
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
  at the other so future maintainers know they are paired.

- [cleanup] dna/brief.py:60-98 — `_build_request` declares its return as
  `tuple[list[dict], list[dict]]` but the dicts are `dict[str, Any]` with
  heterogeneous values (strings, nested dicts, the `cache_control` block
  added at line 88). The looser annotation is what forces the
  `# type: ignore[arg-type]` at lines 156-157 | fix: import the Anthropic
  SDK's `MessageParam` / `TextBlockParam` types (or define local `TypedDict`s
  if the SDK stubs still predate `cache_control`) so the return type is
  precise and the call site loses the `# type: ignore`.

- [cleanup] dna/brief.py:21 — `_ANTHROPIC` is implicitly typed (inferred as
  `Anthropic`). Other singletons in the codebase are explicitly annotated
  (e.g. `dna/embeddings.py:20` annotates `_VOYAGE: voyageai.Client | None`);
  add `_ANTHROPIC: Anthropic = …` for consistency, or
  `_ANTHROPIC: Anthropic | None = None` if you adopt the lazy-singleton fix
  above.

- [cleanup] dna/identity.py:262 — `_ = sa` is a discard to silence the
  "unused import" warning on `import sqlalchemy as sa`. The cleaner pattern
  is to just remove the import until the "future column-level helpers" land
  (YAGNI), or import it locally inside whatever helper needs it. Promote it
  from "comment about future use" to "delete-and-re-add-when-needed".

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — Anthropic + Voyage clients are module-level singletons (lazy for Voyage; eager for Anthropic — see SEV2 above). Sessions are passed in by the caller (worker/router) and not opened inside the module. No file handles, subprocesses, or temp media in this module. |
| 2 Concurrency & scale | ok — `_aembed` correctly wraps the sync Voyage SDK in `asyncio.to_thread` (acknowledges Issue 38 W1 + Issue 68 fixes). `rank_videos` is bounded by `settings.DNA_MAX_CANDIDATE_VIDEOS=500` (Issue B). `_enrich_videos` is batched into 3 IN-queries (no N+1). `identity.upsert_identity` and `profile.confirm_draft` both use `with_for_update()` plus a partial unique index — correctly handle the concurrent-write race. |
| 3 Security & compliance | ok — every query touching a creator-scoped table filters by `creator_id` (builder.py:119, 249, 257, 297; profile.py:53, 83, 109-117, 146-149, 167-172, 184-187; identity.py:32-35, 44-48, 80-86, 97-101). All SQL is SQLAlchemy ORM (parameterized). Logs include `creator_id` UUID only (internal id, not PII), brief token counts, and DNA version numbers — no tokens, no email, no channel identity. The brief's honesty disclaimer is appended to every output (brief.py:27-31, 151, 173) — no virality promise. |
| 4 Clip-quality | ok — Principle 11 ("Audience-fit over generic virality") is structurally enforced: DNA is built from *this* creator's recency-weighted engagement (builder.py:_recency_weight), patterns are inferred from *this* creator's top/bottom videos (builder.py:284-288), and the brief is generated against *this* creator's stated identity + inferred patterns (brief.py:35-57). Principle 10 ("Native length over generic length") shows up at builder.py:75-84 (`_optimal_clip_len_s`) and `optimal_upload_gap_h`. Principle 6 ("Retention curve is ground truth") underpins `_best_source_region` (builder.py:58-72). No clip-start/setup logic lives in this module — that is `clip_engine/`'s job — so the "setup vs aftermath" rubric item is n/a here. |
| 5 Anthropic SDK | mostly ok — prompt caching is attempted with the correct structure (stable instructions first, optional stable identity second, volatile corpus last; `cache_control: ephemeral` on the last stable block — brief.py:82-90). Token usage is logged after every call including cache_read / cache_creation (brief.py:144-150, 160-166). `max_tokens=2000` is set. The module's own docstring (brief.py:1-9) honestly flags that the static prefix is below the 2048-token minimum cacheable size for Sonnet 4.6 — already acknowledged in `docs/DECISIONS.md`, so no new finding. No web-search tool here, which is correct: this call is synthesis from in-DB data, not live research. |
| 6 Cleanliness & typing | mostly ok — cleanup-severity typing/DRY items above. No `print()` calls, no commented-out code blocks, no `TODO`. Most functions are typed; the gaps are in `dna/embeddings.py:30-35` (`_embed`, `_aembed`) and the loose `dict` returns in `dna/brief.py:60`. `_enrich_videos` is the only function over 30 lines that does more than one thing. |
| 7 Error handling / API | n/a — no router endpoints in this module. The module raises `ValueError` at well-defined boundaries (builder.py:272 insufficient data, profile.py:125 no draft, identity validators) which routers translate to HTTP. `RuntimeError` at brief.py:170 if Claude returns no text block — caught by worker. |
| 8 Config & paths | ok — all referenced settings exist in `config.py` (`MIN_VIDEOS_FOR_DNA`, `MIN_SHORTS_FOR_DNA`, `DNA_MAX_CANDIDATE_VIDEOS`, `ANTHROPIC_MODEL`, `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`) and the three DNA-tunable ones are present in `.env.example` with descriptions. No filesystem paths in this module. |

## Module verdict

**NEEDS-WORK** — no BLOCKERs and per-creator isolation is correctly enforced
on every query; the four SEV2s are real (import-time Anthropic client construction,
silently-disabled conflict detector for unmapped niches, missing return types on
Voyage helpers, tenacity reraise default) and should be fixed before public
launch, but none are scale-killers on their own.
