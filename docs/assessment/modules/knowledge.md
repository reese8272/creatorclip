# knowledge — assessed 2026-06-08

## Findings

- [FIXED] knowledge/hooks.py:174-179 — **SEV1 from prior assessment, now resolved.**
  The `cache_control: {"type": "ephemeral"}` marker on the DNA-brief block has been
  removed (lines 174-179 now contain an audit-fix comment explaining the removal).
  The static instructions + DNA brief (~900 tokens) is below Haiku 4.5's 4096-token
  floor; the marker was inert. Fix applied: marker removed, documentation added.
  No regression test yet; DECISIONS.md references the precedent (improvement/brief.py).

- [FIXED] knowledge/chapters.py:182-185 — **SEV1 from prior assessment, now resolved.**
  The `cache_control` breakpoint has been removed from the system block.
  System prompt (~175 tokens) is far below Haiku 4.5's 4096-token floor; the marker
  was inert. Fix applied: marker removed, audit-fix comment added. Same status as hooks.py.

- [SEV2] knowledge/hooks.py:25 + knowledge/chapters.py:22 — `_HAIKU_MODEL` is
  hardcoded to `"claude-haiku-4-5-20251001"` instead of routing through
  `settings.ANTHROPIC_MODEL` or dedicated config keys (`ANTHROPIC_MODEL_HOOK_ANALYSIS`,
  `ANTHROPIC_MODEL_CHAPTERS`). This blocks model rotation, A/B eval, and violates the
  per-call-site config pattern documented in DECISIONS.md and used elsewhere
  (e.g., `settings.ANTHROPIC_MODEL_DNA`). | fix: add `ANTHROPIC_MODEL_HOOK_ANALYSIS`
  and `ANTHROPIC_MODEL_CHAPTERS` to `config.Settings` (default
  `"claude-haiku-4-5-20251001"`), reference them in `.env.example`, replace both
  hardcodes, and add a DECISIONS entry documenting the per-call-site model pattern.

- [SEV2] knowledge/thumbnails.py:148-152 — `analyze_thumbnail_patterns` logs
  token usage with only `input_tokens` and `output_tokens`, omitting `cache_read`
  and `cache_creation` fields. The docstring and design doc note that the multimodal
  prompt is per-call and cannot be cached — this is correct, but the absence of
  cache metrics isn't documented in the code, and the architecture rule "Token usage
  logged after every call" (rubric §5) passes literally while potentially hiding
  whether other Anthropic call sites are violating the mandatory-caching rule.
  | fix: add a one-line comment explaining why caching doesn't apply here (per-call
  prompt), and update the log line to include `cache_read=0 cache_creation=0` for
  consistency across the four knowledge surfaces (titles, thumbnails, hooks, chapters).
  Alternatively, move the static schema-spec portion into a cached system block if
  worthwhile (Sonnet 4.6 is in use here).

- [SEV2] knowledge/titles.py:105-106 + knowledge/thumbnails.py:177-179 — comments
  claim the static instructions + DNA brief total "~2300 tokens" and clear the
  "2048-token minimum". DECISIONS.md (line 2207) corrects the Sonnet 4.6 floor to
  **1024 tokens**, not 2048. The actual measured size of these blocks (from prior
  assessment context) is ~1100–1300 tokens — it clears 1024 but does NOT clear 2048.
  The stale comment misleads future contributors sizing prompts against the wrong
  floor. | fix: update both comments to reference the correct 1024-token minimum and
  replace the "~2300 tokens" claim with the measured value (~1100–1300 tokens).

- [SEV2] knowledge/hooks.py:169 — the f-string arithmetic for the retention drop
  calculation includes `(creator_median_at_drop or 0)` in the denominator of the
  percentage-point delta. The parameter `creator_median_at_drop` is typed
  `float | None` (line 153), and the branch guarding this code block (line 164) only
  checks `retention_drop_at_s is not None`, not `creator_median_at_drop is not None`.
  When `creator_median_at_drop` is None, the arithmetic silently evaluates to
  `(0 - retention_at_drop) * 100` and the prompt reads "X pp below median" with a
  misleading zero or negative number — Claude's diagnosis is built on wrong data.
  | fix: guard `creator_median_at_drop is not None` before emitting the median delta;
  otherwise drop the "pp below median" tail and only include the video-side retention
  number. Add a comment on line 164 explaining the guard.

- [cleanup] knowledge/titles.py:90-92 + knowledge/thumbnails.py:162-164 —
  `_extract_transcript_summary` and `_extract_transcript_hook` are now single-line
  wrappers around `knowledge.util.extract_transcript_text`. The wrapper shims exist
  only to preserve the import name in `worker/tasks.py:2329` and `worker/tasks.py:2466`.
  The util.py extraction (the stated point of Issue 130/131) is half-done — the
  wrappers are dead weight. | fix: delete both wrappers and update the two import
  sites in `worker/tasks.py` to import `extract_transcript_text` directly from
  `knowledge.util` (with a local alias if a different `max_chars` default is needed).

- [cleanup] knowledge/titles.py:202, knowledge/thumbnails.py:280,
  knowledge/hooks.py:208, knowledge/chapters.py:204 — every streaming call does
  `_ANTHROPIC.with_options(timeout=120.0)` (or 60.0 in chapters) but the module-level
  `_ANTHROPIC` was already constructed with the same `httpx.Timeout(120.0, connect=10.0)`.
  The `with_options(...)` call is a no-op view that adds visual noise without changing
  behavior. | fix: drop the `with_options(...)` call and pass `_ANTHROPIC` directly
  to `stream_and_emit(...)`.

- [cleanup] knowledge/titles.py:101 + knowledge/thumbnails.py:173 —
  `_build_request` and `_build_concepts_request` return bare `tuple` (no inner types).
  The calling code in `generate_title_suggestions` (line 196) and
  `generate_thumbnail_concepts` (line 274) unpacks them as `system, tools, messages`,
  but the type annotation does not document this contract. | fix: annotate both as
  `-> tuple[list[dict], list[dict], list[dict]]`.

- [cleanup] knowledge/thumbnails.py:100 (`analyze_thumbnail_patterns` → `dict`) +
  knowledge/thumbnails.py:89 (`_empty_patterns()` → `dict`) — both return a bare `dict`
  with a documented key shape (`face_present`, `dominant_emotions`, `text_overlay_style`,
  `typical_colors`, `composition_pattern`, `channel_thumbnail_signature`). The shape is
  enforced only by string conventions in the code. | fix: define a `TypedDict
  ChannelThumbnailPatterns` in `knowledge/util.py` with the six documented keys,
  annotate both functions with it, and update `_build_concepts_request` to take
  `patterns: ChannelThumbnailPatterns` instead of `patterns: dict`.

- [cleanup] knowledge/util.py — three extraction helpers (`extract_transcript_text`,
  `extract_transcript_excerpt`, `get_transcript_segments`) share ~6 lines of identical
  scaffolding (null-guard, segment list lookup, text filter, join+slice). The
  duplication is KISS-acceptable as is, but the contract between `extract_transcript_text`
  and `extract_transcript_excerpt` (both extract text, one with time filtering) could
  collapse into a single function: `extract_transcript_text(segments_jsonb, *,
  max_chars=1500, before_s=None)`. This would also clarify whether `get_transcript_segments`
  has any live callers (only `worker/tasks.py:2832` uses it). | fix: optional —
  if no other callers depend on the current signature, merge the two functions;
  otherwise, document the time-filter parameter and remove `get_transcript_segments`
  if unused.

- [cleanup] knowledge/__init__.py — empty file. Acceptable as a namespace marker, but
  the module exports four public entry points: `generate_title_suggestions`,
  `analyze_thumbnail_patterns`, `generate_thumbnail_concepts`, `analyze_hook`,
  `generate_chapters`, plus several `parse_*` helpers. Currently, `worker/tasks.py`
  and `routers/thumbnails.py` reach directly into submodules. | fix: optional —
  add `__all__` and re-exports to `knowledge/__init__.py` so consumers can do
  `from knowledge import analyze_hook` instead of late imports; otherwise, leave as is.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | **ok** — `_ANTHROPIC` is a module-level singleton in each of four files; no per-call client construction. Stream context manager (`worker/anthropic_stream.py:78`) guarantees release. |
| 2 Concurrency & scale | **ok** — all entry points are sync, invoked via `asyncio.to_thread` (lines 2372, 2547, 2573, 2768, 2887 in worker/tasks.py; line 131 in routers/thumbnails.py). No hidden blocking calls inside `async def`. |
| 3 Security & compliance | **ok** — no PII or token in logger calls (only token counts and labels). No SQL. Per-creator isolation enforced at caller sites. No virality language; explicit honesty disclaimers in titles.py and thumbnails.py. ANTHROPIC_API_KEY loaded via pydantic settings, never logged. |
| 4 Clip-quality | **n/a** — knowledge module is advisory (title / thumbnail / hook / chapter generation), not on clip-extraction path. |
| 5 Anthropic SDK | **IMPROVED, 1 SEV2 remains** — The two SEV1 cache-floor defects (hooks.py, chapters.py) are **fixed**: inert markers removed, audit comments added. Stale cache-floor comments in titles.py / thumbnails.py remain (marked SEV2). Token usage logged on all four call sites; `cache_read` and `cache_creation` fields missing on `analyze_thumbnail_patterns` (marked SEV2). web_search tool wired on three of four; thumbnails multimodal-only (correct, no tools). |
| 6 Cleanliness & typing | **5 cleanup items** — wrapper shims in titles.py / thumbnails.py are dead code; bare `tuple` and `dict` returns need inner types; missing `ChannelThumbnailPatterns` TypedDict; redundant `with_options` calls on four streaming sites; util.py extraction is half-done. No TODO, no commented-out code, no `print()`. |
| 7 Error handling / API | **n/a** — no FastAPI router in `knowledge/`. Public surface is library functions raising `ValueError` on bad input (parse_*) and letting SDK exceptions propagate for Celery retry. Error contract consistent across four features. |
| 8 Config & paths | **SEV2** — `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `ANTHROPIC_WEB_SEARCH_TOOL` all route through `config.Settings`; no file-system paths in module. **But** `_HAIKU_MODEL` is hardcoded (SEV2) instead of per-call-site config keys (`ANTHROPIC_MODEL_HOOK_ANALYSIS`, `ANTHROPIC_MODEL_CHAPTERS`). `.env.example` needs entries for those keys. |

## Module verdict

IMPROVING — The two SEV1 prompt-caching defects (hooks.py:176, chapters.py:182)
are **now resolved** by removing the inert `cache_control` markers and documenting
the reason in audit comments. One SEV2 config defect remains (hardcoded Haiku model
strings block per-call-site override pattern); another SEV2 affects token visibility
(cache metrics missing from thumbnails.py). Five cleanup items address typing,
dead code, and stale comments. The caching architecture rule ("mandatory prompt
caching") is no longer silently violated, but the per-call-site model-override
pattern (documented in DECISIONS.md) is still not applied here.

