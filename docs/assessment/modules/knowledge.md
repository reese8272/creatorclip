# knowledge — assessed 2026-06-16

## Findings

### Prior SEV1s — both VERIFIED CLOSED (Issue 138)

- [RESOLVED — was SEV1 #5] knowledge/chapters.py:213 — `max_tokens` is now **2000**
  (was 512), and `description_block` was **removed** from the `_SYSTEM_INSTRUCTIONS`
  output schema (chapters.py:47-56 now asks only for the `chapters` array). Verified
  `parse_chapters` still rebuilds `description_block` deterministically in Python when
  the model omits it (chapters.py:153-157: `if not description_block:` →
  `"\n".join(f"{c['timestamp_formatted']} {c['title']}" ...)`). Output is therefore
  complete on 1h+ videos: the model spends its whole budget on the chapters array and
  Python regenerates the description block. The fix comment at chapters.py:208-212
  accurately documents the rationale. Correctness confirmed.

- [RESOLVED — was SEV1 #6] knowledge/titles.py:121-135 + knowledge/thumbnails.py:201-216
  — the `cache_control: {"type": "ephemeral"}` markers are **removed** from both
  `_build_request` (titles) and `_build_concepts_request` (thumbnails). Both system
  blocks are now plain `{"type": "text", "text": ...}` with no `cache_control` key.
  The in-code comments (titles.py:43-44, 104-107, 124-128; thumbnails.py:176-179,
  204-206) now correctly state the prefix (~1,550 tokens) is **below** Sonnet 4.6's
  2048-token floor and that a marker would be inert — they no longer falsely claim the
  prefix clears the floor. This matches the hooks.py precedent (marker removed for the
  Haiku 4.5 4096 floor, hooks.py:174-179). The root-cause doc error is also fixed:
  docs/DECISIONS.md now records the Sonnet 4.6 floor as 2048 (lines 13-15, 2330,
  601-602) with an explicit Issue-138 correction note (1024 is the Sonnet *4.5* floor).
  Both fixes are correct and internally consistent.

### Carried-forward open findings (re-verified against current code)

- [SEV2] knowledge/hooks.py:25 + knowledge/chapters.py:22 — `_HAIKU_MODEL` still
  hardcoded to `"claude-haiku-4-5-20251001"` in both files. The ID is valid, but it
  bypasses the per-call-site config pattern (every other site uses
  `settings.ANTHROPIC_MODEL`; config.py:51 only defines `ANTHROPIC_MODEL`, no
  hook/chapter overrides exist). Blocks model rotation / A-B without a code change.
  | fix: add `ANTHROPIC_MODEL_HOOK_ANALYSIS` and `ANTHROPIC_MODEL_CHAPTERS` to
  `config.Settings` (default `"claude-haiku-4-5-20251001"`), document in `.env.example`,
  replace both hardcodes, log the pattern in docs/DECISIONS.md.

- [cleanup] knowledge/titles.py:38-41 + knowledge/thumbnails.py:43-46 — `_DISCLAIMER`
  remains dead code in BOTH files: defined, never imported or appended anywhere
  (verified repo-wide grep — the only `_DISCLAIMER` consumers are dna/brief.py,
  analysis/brief.py, improvement/brief.py, which define their own). The titles.py
  module docstring (line 14) still claims "The honesty disclaimer is always appended by
  Python — never left to the LLM", which is false for this module: the SSE/done payload
  carries no disclaimer, while the equivalent honesty text is hardcoded separately in
  static/analysis.html — backend and frontend copies can drift (DRY). | fix: either
  append `_DISCLAIMER` to the done payload in the worker tasks and render from it, or
  delete both constants and correct the titles.py docstring.

- [cleanup] knowledge/titles.py:204, knowledge/thumbnails.py:280, knowledge/hooks.py:208,
  knowledge/chapters.py:203 — `_ANTHROPIC.with_options(timeout=...)` before every
  streaming call. The module clients are built with `httpx.Timeout(X, connect=10.0)`;
  `with_options(timeout=120.0)` (titles/thumbnails/hooks) and `with_options(timeout=60.0)`
  (chapters) replace that with a flat scalar, silently loosening the connect timeout
  from 10s to the full read budget. | fix: drop the `with_options(...)` calls and pass
  `_ANTHROPIC` directly (the module-level timeout already matches).

- [cleanup] knowledge/hooks.py:164-169 — `(creator_median_at_drop or 0)` arithmetic can
  emit a misleading "Xpp below median" line if `creator_median_at_drop` is None while
  `retention_drop_at_s` is not. The sole caller (worker/tasks.py) computes a non-None
  median whenever `drop_at_s` is not None, so unreachable today, but the `float | None`
  signature permits it for any future caller. | fix: guard
  `creator_median_at_drop is not None` before emitting the median-delta tail.

- [cleanup] knowledge/titles.py:91-93 + knowledge/thumbnails.py:162-164 —
  `_extract_transcript_summary` / `_extract_transcript_hook` are one-line wrapper shims
  over `knowledge.util.extract_transcript_text`, kept only to preserve import names in
  worker/tasks.py:2387/2524 (and the test imports in tests/test_titles.py,
  tests/test_thumbnails.py). | fix: import `extract_transcript_text` directly at the
  two call sites, update the two tests, and delete the shims.

- [cleanup] knowledge/titles.py:35 + knowledge/hooks.py:34 — dead constants:
  `_GENERATE_N = 10` and `TRANSCRIPT_EXCERPT_S = 60.0` are never referenced (the "10"
  lives in the prompt text; the 60.0 is passed literally by the caller). | fix: delete,
  or reference them where the values are used.

- [cleanup] knowledge/titles.py:102 + knowledge/thumbnails.py:173 — `_build_request`
  / `_build_concepts_request` still return bare `tuple`. | fix: annotate
  `-> tuple[list[dict], list[dict], list[dict]]`.

- [cleanup] knowledge/thumbnails.py:89, 100, 142-159 — `analyze_thumbnail_patterns`
  and `_empty_patterns` return bare `dict` with a six-key documented shape enforced only
  by convention; the token log line (148-152) omits cache fields with no comment
  explaining why (the result is Redis-cached 24h per channel — `PATTERNS_CACHE_TTL`,
  line 38 — so the multimodal call runs at most once/day/channel and prompt caching is
  genuinely N/A). | fix: add a `ChannelThumbnailPatterns` TypedDict in knowledge/util.py
  and a one-line "per-day multimodal call — prompt caching N/A" comment at the log line.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — `_ANTHROPIC` is a module-level singleton in all four files; no per-call construction; streaming via the shared `stream_and_emit` context manager; no temp files in this module |
| 2 Concurrency & scale | ok — prior SEV1 (chapters max_tokens) is fixed; all entry points sync and invoked via `asyncio.to_thread` from worker tasks; inputs bounded (`_SEGMENT_MAX_CHARS`, `_DNA_BRIEF_MAX_CHARS`, 10-thumbnail cap, 20-curve cap at caller); no blocking call inside `async def` |
| 3 Security & compliance | ok — no SQL in module; logger lines carry token counts only, no PII/tokens; API key via pydantic settings, never logged; per-creator isolation enforced at caller sites (verified worker/tasks.py creator_id checks); all generated copy hedged, honesty constraints in prompts, no virality language (but see `_DISCLAIMER` dead-code cleanup) |
| 4 Clip-quality | n/a (advisory title/thumbnail/hook/chapter surfaces, not on the clip-extraction path) |
| 5 Anthropic SDK | ok — both prior SEV1 cache defects CLOSED: chapters max_tokens=2000 + description_block dropped from schema; titles/thumbnails inert cache_control markers removed and comments corrected; hooks marker removal still in place (hooks.py:174-179). Token usage logged after every streaming call and the multimodal call; web_search wired where intended (titles/thumbnails/hooks); SDK pinned anthropic==0.105.2 (no shape change). Open: `_HAIKU_MODEL` hardcoded (SEV2 config item below) |
| 6 Cleanliness & typing | 7 cleanup items — dead `_DISCLAIMER` + false docstring, dead constants, wrapper shims, bare `tuple`/`dict` returns, connect-timeout-loosening `with_options`, hooks median-delta guard. No TODO, no commented-out code, no `print()` |
| 7 Error handling / API | n/a (no router; library functions raise `ValueError`/SDK errors for Celery retry — consistent contract; tests exist: tests/test_titles.py, test_chapters.py, test_hooks.py, test_thumbnails.py) |
| 8 Config & paths | 1 SEV2 — `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` / `ANTHROPIC_WEB_SEARCH_TOOL` via settings + `.env.example`; no filesystem paths in module; `_HAIKU_MODEL` hardcode bypasses config (no `ANTHROPIC_MODEL_CHAPTERS`/`_HOOK_ANALYSIS` in config.py:51) |

## Module verdict

NEEDS-WORK — Both Issue-138 SEV1 fixes are verified correct and complete:
chapters.py now caps output at 2000 tokens with `description_block` dropped from the
schema and rebuilt in Python (no truncation on long-form videos), and the inert
`cache_control` markers are removed from titles.py/thumbnails.py with the comments and
docs/DECISIONS.md (2048 Sonnet 4.6 floor) corrected. No BLOCKER or SEV1 remains. One
SEV2 (hardcoded `_HAIKU_MODEL` in hooks.py/chapters.py bypasses config) and seven
cleanups (dead `_DISCLAIMER` + false docstring, dead constants, wrapper shims, bare
return types, connect-timeout-loosening `with_options`, hooks median guard) are open.
