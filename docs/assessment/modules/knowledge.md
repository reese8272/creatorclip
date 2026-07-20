# knowledge — assessed 2026-07-20 (post-fix)

Slice: `knowledge/{chapters,clip_captions,clip_explain,clip_titles,hooks,thumbnails,titles,util}.py`, `knowledge/__init__.py` (empty).
Prior run: 2026-07-20 (morning). Diff scrutinized: `git diff ca3305c..e92b93a -- knowledge/ worker/anthropic_stream.py`
(chapters/hooks/thumbnails/titles changed — Issue 361 llm-tail: shared `stream_until_final`
pause_turn helper + chapters untrusted-content posture).

Load-bearing claims verified by reading, not assuming:
- `worker/anthropic_stream.py:201-255` `stream_until_final`: sums all four usage keys across
  EVERY round (`usage[k] += round_usage.get(k, 0)` per round — billing-correct, no
  final-round-only figure); `warn_if_truncated` fires per round inside `stream_message`
  (anthropic_stream.py:184); the `for/else` logs `round_cap_warning % max_rounds` and returns the
  last paused message — behavior-identical to the former inline thumbnails loop.
- All three knowledge web-search call sites now use it with `max_rounds=5` and a per-builder cap
  warning: titles.py:245-259, hooks.py:248-262, thumbnails.py:329-339. Each guards
  `final_msg is None` and empty `text_blocks` before taking `text_blocks[-1].text`
  (titles.py:259-264, hooks.py:261-266, thumbnails.py:343-348). improvement/brief.py:144 consumes
  the same helper (outside this slice) — the consolidation claim in the helper docstring holds.
- `stream_and_emit` is NOT dead: still the correct (no-tools, no-pause_turn-risk) path for
  chapters.py:217, dna/brief.py:150, analysis/brief.py:166.
- Helper is unit-tested: `tests/test_anthropic_stream.py:394` (continues on pause_turn + sums
  usage) and `:435` (bounds rounds + warns).
- Floor-gated `dna_system_block` survived untouched (util.py:39-61; call sites titles.py:135,
  thumbnails.py:225, clip_titles.py:157, clip_captions.py:129, clip_explain.py:158; tested in
  tests/test_knowledge_util.py).

## Resolved since 2026-07-20 (morning)
- **[SEV2 → FIXED] titles/hooks web-search streams lacked a pause_turn continuation.**
  Exactly the recommended fix landed (commit 319d53d): the thumbnails inline loop was extracted
  to `worker/anthropic_stream.py:201` `stream_until_final(client, task_id, *, …, max_rounds=5,
  round_cap_warning=…)` and adopted by titles.py:245, hooks.py:248, AND thumbnails.py:329
  (removing the function-local loop). Usage accumulation across rounds, per-round
  `warn_if_truncated`, and the round-cap warning all verified present; docstrings in titles/hooks
  updated to say usage sums across rounds.
- **[SEV2 → FIXED] chapters.py unwrapped transcript + missing UNTRUSTED_CONTENT_POLICY.**
  chapters.py:39-40 now prepends `UNTRUSTED_CONTENT_POLICY` to `_SYSTEM_INSTRUCTIONS`
  (f-string, JSON schema braces correctly doubled) and chapters.py:206 wraps the joined segment
  lines as `wrap_untrusted("video_transcript_segments", …)` ahead of the instruction sentence —
  matching the wrap-before-instructions guidance in util.py:118-121. The util.py:15 "all nine
  builders" comment is now accurate without edit. Still correctly NO cache marker (~230-token
  policy + instructions far below Haiku 4.5's 4,096 floor; audit comment chapters.py:189-192
  retained).

## Findings

- [cleanup] (carry-forward) 7 separate module-level `AsyncAnthropic` clients —
  chapters.py:28, clip_captions.py:39, clip_explain.py:42, clip_titles.py:37, hooks.py:32,
  thumbnails.py:38, titles.py:43. | fix: one shared `AsyncAnthropic` in `knowledge/_client.py`;
  keep per-call `.with_options(timeout=…)` (shares the underlying pool).
- [cleanup] (carry-forward) Identical `usage_dict` construction duplicated at
  clip_titles.py:~278, clip_captions.py:225-229, clip_explain.py:279-283 (same 4-key
  `getattr(response.usage, …) or 0` block; anthropic_stream.py:113/186 builds the same shape
  twice more). | fix: `usage_from_response(usage) -> dict[str, int]` in knowledge/util.py (or
  observability) used by all.
- [cleanup] (carry-forward) Per-call `logger.info("… tokens: in=%d cached_read=%d
  cached_write=%d out=%d", …)` block duplicated in all 7 builders (chapters.py:233,
  clip_captions.py:230, clip_explain.py:284, clip_titles.py:282, hooks.py:268,
  thumbnails.py:349, titles.py:265). | fix: fold into `record_llm_metric` /
  `record_llm_tokens`.
- [cleanup] NEW (post-consolidation) — the 6-line `final_msg is None` / `text_blocks` extraction
  epilogue is now itself triplicated verbatim at titles.py:259-264, hooks.py:261-266,
  thumbnails.py:343-348 (only the message strings differ). | fix: add
  `final_text_from(msg, context: str) -> str` next to `stream_until_final` (or an
  `extract_text=True` mode) so the helper family owns the extraction as `stream_and_emit`
  already does.
- [cleanup] NEW — thumbnails.py:312-313 docstring still says usage is "the token-count dict from
  ``stream_and_emit``"; titles/hooks docstrings were updated to the sums-across-rounds wording
  but thumbnails' was not. | fix: mirror the titles.py:228-231 wording.
- [cleanup] (carry-forward) Bare unparameterized `-> tuple:` on titles.py:118 `_build_request`
  and thumbnails.py:199 `_build_concepts_request` (clip builders use
  `-> tuple[list[dict], list[dict]]`). | fix: `-> tuple[list[dict], list[dict], list[dict]]`.
- [cleanup / needs-runtime-confirmation] (carry-forward) `ANTHROPIC_WEB_SEARCH_TOOL` still pins
  `web_search_20260209` (config.py:132) with `allowed_callers=["direct"]` forced at
  titles.py:148, thumbnails.py:240, hooks.py:219 to suppress dynamic filtering. Works per the
  2026-07-01 live-doc check; a live smoke asserting a non-empty final text block remains the
  guard against a routing regression on this version.
- [cleanup] (carry-forward) hooks.py:184 — inside the `retention_drop_at_s is not None` branch,
  `f"video at {retention_at_drop:.1%}"` raises TypeError if `retention_at_drop` is None while
  the sibling values on lines 185-186 are `or 0`-guarded. The pair always co-varies from
  `compute_retention_drop` (both-None or both-set), so latent only. | fix:
  `{(retention_at_drop or 0):.1%}` to match the neighbors.

Notes (not findings):
- Round-cap path: if `stream_until_final` exhausts `max_rounds`, the returned message is the
  last PAUSED turn — its final text block may be partial, so `parse_candidates` /
  `parse_hook_report` can still raise ValueError. This is the same bounded-degradation contract
  the inline thumbnails loop had (cap warning logged first); acceptable by design, not a
  regression.
- `dna_system_block` measures only Block 1 + Block 2 chars; for web-search builders the tool
  definition also precedes the breakpoint. Error is in the conservative direction (marker
  occasionally omitted just above the true floor), never inert — acceptable per Issue-315.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | 1 cleanup (7 duplicate AsyncAnthropic clients, carried); async clients bound to the worker's singleton loop (verified prior run, unchanged); no DB/temp-media here |
| 2 Concurrency & scale | ok — the pause_turn SEV2 is FIXED via shared `stream_until_final` (bounded at max_rounds+1 calls, tested); builders async end-to-end; inputs bounded (images `[:10]`, transcript char caps) |
| 3 Security & compliance | ok — the chapters SEV2 is FIXED (policy prepended + transcript wrap_untrusted); all 7 builders now wrap every untrusted surface in the user turn; verbose full-prompt logging double-gated off in prod; no tokens/PII/SQL; honesty disclaimers Python-appended in every builder |
| 4 Clip-quality | n/a (generation module) — clip_explain still constrains `cited_principle` to the canonical enum |
| 5 Anthropic SDK | ok — floor-gated 1h cache marker on the 5 Sonnet builders, correctly none on the Haiku pair; usage summed across pause_turn rounds and logged + metered after every call; `warn_if_truncated` per round; structured outputs only on non-web-search paths (citations incompatibility) |
| 6 Cleanliness & typing | 8 cleanup (6 carried + 2 new minor: triplicated text-extraction epilogue, stale thumbnails docstring); no TODO/print/pdb |
| 7 Error handling / API | n/a (not a router; typed SDK errors propagated to callers as documented) |
| 8 Config & paths | ok — models + web-search tool version config-driven and in .env.example; no filesystem paths; thumbnail URLs absolute https |

## Module verdict
clean — both morning SEV2s are verifiably fixed exactly as prescribed: titles/hooks/thumbnails
now share the tested `stream_until_final` pause_turn loop (usage summed across rounds, per-round
truncation warning, round-cap log all intact through the consolidation), and chapters carries the
untrusted-content policy plus a wrap_untrusted transcript. Only DRY/typing cleanups remain
(7 clients, duplicated usage/log/extraction blocks, two bare tuple annotations).
