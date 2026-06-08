# knowledge — assessed 2026-06-07

## Findings

- [SEV1] knowledge/hooks.py:176-180 — `cache_control: {"type": "ephemeral"}` on the
  DNA-brief block, but the call runs against Haiku 4.5
  (`_HAIKU_MODEL = "claude-haiku-4-5-20251001"`, line 25) whose cacheable-prefix
  minimum is 4096 tokens (see docs/DECISIONS.md). System block 1 (~600 chars ≈ 150
  tok) + DNA brief (≤3000 chars ≈ 750 tok) ≈ 900 tokens — well below the floor, so
  the cache breakpoint is **inert** on every call. The mandatory-prompt-caching
  architecture rule is silently violated. | fix: either (a) move hook analysis to
  Sonnet (1024-token floor — would engage) and bump `_HAIKU_MODEL` to `settings.ANTHROPIC_MODEL`,
  or (b) drop the cache_control breakpoint and add a code comment that hook analysis
  is too short to benefit from caching. Log token-usage `cache_read` should be
  asserted >0 in an integration test once decided.

- [SEV1] knowledge/chapters.py:182-188 — same defect as hooks.py: `cache_control`
  breakpoint on the static system block, but Haiku 4.5 with a system prompt of
  ~700 chars (~175 tokens) is far below the 4096-token Haiku floor. The cache
  marker is inert. | fix: drop the `cache_control` entry on chapters.py — chapter
  generation is one-shot per video and the prompt is too small for caching to
  engage on Haiku. Update the docstring (line 3) which currently claims "one
  cached system block".

- [SEV2] knowledge/hooks.py:25 + knowledge/chapters.py:22 — `_HAIKU_MODEL` is a
  hardcoded module-level string. Every other Anthropic call site routes through
  `settings.ANTHROPIC_MODEL` / dedicated settings keys (e.g. `ANTHROPIC_MODEL_DNA`),
  which is the DECISIONS.md pattern for per-call-site model overrides. Hardcoding
  blocks model rotation, A/B eval, and the per-call-site config pattern already
  used elsewhere. | fix: add `ANTHROPIC_MODEL_HOOK_ANALYSIS` and
  `ANTHROPIC_MODEL_CHAPTERS` to `config.Settings` (default
  `"claude-haiku-4-5-20251001"`), reference them in `.env.example`, and replace
  both hardcodes.

- [SEV2] knowledge/thumbnails.py:142-146 — `analyze_thumbnail_patterns` calls
  `_ANTHROPIC.messages.create(...)` with no `cache_control` on the multimodal
  content and no `tools` arg. The system prompt is per-call (URLs + channel
  title embedded), so caching genuinely cannot help — but the absence isn't
  documented and there is **no token-usage log line for the cache_read /
  cache_creation fields** (only `input_tokens` and `output_tokens`, line 148).
  Architecture rule "Token usage logged after every call" passes literally but
  hides whether the rule "Prompt caching used (mandatory)" is being violated
  on this Anthropic call. | fix: add a one-line comment explaining the prompt
  here is per-call so caching cannot engage, and either (a) extend the log line
  to include `cache_read=0 cache_creation=0` for uniformity with the other three
  call sites, or (b) move the static portion of the schema-spec text into a
  cached system block (it is large enough and the model is Sonnet 4.6).

- [SEV2] knowledge/titles.py:124-125 + knowledge/thumbnails.py:204-205 — code
  comments claim system blocks 1+2 are "~2300 tokens" and clear the
  "2048-token minimum". Both numbers are stale: docs/DECISIONS.md records the
  Sonnet 4.6 cacheable-prefix minimum as **1024 tokens** (the 2048 figure was
  corrected), and the actual block 1+2 size on these two files is ~1100–1300
  tokens — it clears 1024 but does NOT clear 2048. The comment misleads anyone
  later sizing prompts against the wrong floor. | fix: update both comments to
  reference 1024 and remove the "~2300 tokens" claim or replace it with the
  measured value.

- [SEV2] knowledge/hooks.py:169 — f-string arithmetic
  `((creator_median_at_drop or 0) - (retention_at_drop or 0)) * 100` silently
  produces a misleading negative or zero number when `creator_median_at_drop`
  is `None` (the parameter is typed `float | None`). The branch that enters
  this block only checks `retention_drop_at_s is not None`, not the median.
  In that case the prompt will say e.g. "0.0pp below median" while
  `retention_drop_at_s` indicates a real drop — Claude's diagnosis will be
  built on incorrect data. | fix: guard `creator_median_at_drop is not None`
  before emitting the median delta; otherwise drop the "pp below median" tail
  and only include the video-side number.

- [cleanup] knowledge/titles.py:90-92 + knowledge/thumbnails.py:162-164 —
  `_extract_transcript_summary` and `_extract_transcript_hook` are now trivial
  one-line wrappers around `knowledge.util.extract_transcript_text`. The
  wrappers exist only to preserve the import name in `worker/tasks.py:2329`
  and `worker/tasks.py:2466`. The `util.py` extraction (the stated point of
  Issue 130/131) is half-done — the wrappers are dead weight. | fix: delete
  both private wrappers and update the two import sites in `worker/tasks.py`
  to import `extract_transcript_text` directly from `knowledge.util` (with a
  local alias if a different `max_chars` default is needed at the call site).

- [cleanup] knowledge/titles.py:202, knowledge/thumbnails.py:280,
  knowledge/hooks.py:203, knowledge/chapters.py:201 — every call does
  `_ANTHROPIC.with_options(timeout=120.0)` (or 60.0 in chapters) but the
  module-level `_ANTHROPIC` was already constructed with the same
  `httpx.Timeout(120.0, connect=10.0)`. The `with_options(...)` call is a
  no-op view that adds noise without changing behaviour. | fix: drop the
  `with_options(...)` line and pass `_ANTHROPIC` directly to `stream_and_emit`.

- [cleanup] knowledge/titles.py:101 + knowledge/thumbnails.py:173 —
  `_build_request` / `_build_concepts_request` return bare `tuple`. mypy
  --strict-equivalent gates will not catch the missing inner types. | fix:
  annotate as `-> tuple[list[dict], list[dict], list[dict]]`.

- [cleanup] knowledge/thumbnails.py:100 (`analyze_thumbnail_patterns -> dict`)
  + knowledge/thumbnails.py:89 (`_empty_patterns() -> dict`) — both return a
  bare `dict` despite the docstring and other code referring to a
  `ChannelThumbnailPatterns` shape. The shape is enforced by string
  conventions only. | fix: define a `TypedDict ChannelThumbnailPatterns` in
  `knowledge/util.py` with the six known keys and annotate both functions
  with it; `_build_concepts_request` should take it as `patterns:
  ChannelThumbnailPatterns` instead of `dict`.

- [cleanup] knowledge/util.py — three small extraction helpers, two of which
  share ~6 lines of identical scaffolding (segments_jsonb null-guard, segs
  list lookup, parts filter, join+slice). KISS-acceptable as is, but the
  duplication between `extract_transcript_text` and
  `extract_transcript_excerpt` could collapse into one function with an
  optional `max_s: float | None = None` time filter. | fix: optional —
  collapse to a single `extract_transcript_text(segments_jsonb, *,
  max_chars=1500, before_s=None)`; this also removes `get_transcript_segments`
  if no caller needs the raw list (only worker/tasks.py:2832 uses it).

- [cleanup] knowledge/__init__.py — empty file. Acceptable as a namespace
  marker, but consider exporting the public API (`generate_title_suggestions`,
  `analyze_thumbnail_patterns`, `generate_thumbnail_concepts`, `analyze_hook`,
  `generate_chapters`, parse_* helpers) so consumers can do
  `from knowledge import analyze_hook` instead of reaching into submodules
  via late imports in worker/tasks.py. | fix: optional — add `__all__` and
  re-exports if a public surface is desired; otherwise leave as is.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — `_ANTHROPIC` is a module-level singleton in each of the four feature files; no per-call client construction. Stream context manager (`worker/anthropic_stream.py:78`) guarantees release of the streaming connection. |
| 2 Concurrency & scale | ok — all four entry points are sync functions, invoked from async callers via `asyncio.to_thread` (worker/tasks.py:2372, 2547, 2573, 2768, 2887; routers/thumbnails.py:131). No hidden blocking calls inside an `async def`. The blocking sync SDK call is deliberately off-loop. |
| 3 Security & compliance | ok — no PII or token in any logger call (only token-count integers and stage labels). No SQL in this module; per-creator isolation is the caller's responsibility and is satisfied at worker/tasks.py and routers/thumbnails.py. No virality language anywhere; explicit honesty disclaimer strings in titles.py and thumbnails.py. `ANTHROPIC_API_KEY` loaded via pydantic settings, never logged. |
| 4 Clip-quality | n/a — knowledge module is advisory (title / thumbnail / hook / chapter generation), not on the clip-extraction path. |
| 5 Anthropic SDK | **2 SEV1 + 2 SEV2** — `cache_control` breakpoints on hooks.py and chapters.py never engage because Haiku 4.5's 4096-token cacheable-prefix minimum is well above the actual prompt size; mandatory-prompt-caching rule is silently violated on two of four feature surfaces. Stale comments in titles.py / thumbnails.py reference the corrected 2048→1024 minimum. Token usage logged on all four call sites (correctly). web_search tool wired through `settings.ANTHROPIC_WEB_SEARCH_TOOL` on three of four. The fourth (`analyze_thumbnail_patterns`) is multimodal-only with no tools — appropriate. |
| 6 Cleanliness & typing | 5 cleanup findings — `util.py` extraction is half-done (two wrapper shims left in titles.py / thumbnails.py); `_HAIKU_MODEL` hardcoded twice instead of settings-driven; bare `tuple` and `dict` returns on three public-ish helpers; no `ChannelThumbnailPatterns` TypedDict; redundant `with_options` calls on all four streaming sites. No TODO, no commented-out code, no `print()`. |
| 7 Error handling / API | n/a — no FastAPI router lives in `knowledge/`; the public surface is library functions that raise `ValueError` on bad input (parse_*) and let SDK exceptions propagate for Celery retry. The error contract is consistent across the four features. |
| 8 Config & paths | partial — `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `ANTHROPIC_WEB_SEARCH_TOOL` all come through `config.Settings`; no file-system paths in module. **But** `_HAIKU_MODEL` is hardcoded (see SEV2) instead of `ANTHROPIC_MODEL_HOOK_ANALYSIS` / `ANTHROPIC_MODEL_CHAPTERS` settings keys that should be in `.env.example`. |

## Module verdict

NEEDS-WORK — Two SEV1 prompt-caching defects (cache breakpoints on Haiku 4.5
calls are inert because the prompt is far below the 4096-token floor) mean the
"prompt caching mandatory" architecture rule is silently violated on hooks.py
and chapters.py; util.py extraction is only half-applied; hardcoded Haiku
model strings break the per-call-site config pattern documented in DECISIONS.md.
None of these ship a bug at first request, but they undermine the caching
economics that the architecture depends on at scale.
