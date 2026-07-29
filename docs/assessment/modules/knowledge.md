# knowledge — assessed 2026-07-29 (ready-pass delta)

Slice: `knowledge/{chapters,clip_captions,clip_explain,clip_titles,hooks,thumbnails,titles,util}.py`, `knowledge/__init__.py` (empty).
Prior run: 2026-07-20 (post-fix, clean). Delta scrutinized: `git diff e92b93a..HEAD -- knowledge/`
(single commit 452a700, w2/billing-audit: `has_1h_cache_marker` in util.py; `analyze_thumbnail_patterns`
now returns `(patterns, usage)`; five features flag `cache_1h` in their usage dicts) plus the
billing consumers (`billing/ledger.py`, `routers/thumbnails.py`, `routers/clips.py`,
`worker/tasks.py` title/thumbnail regions, `chat/tools.py`).

## Delta 2026-07-29 — w2/billing-audit changes, verified by reading

- **Signature change `analyze_thumbnail_patterns -> tuple[dict, dict]`: no missed caller.**
  Repo-wide grep finds exactly two production call sites, both destructure the tuple:
  `routers/thumbnails.py:235` (inside `_compute_and_bill`, billed via `record_llm_usage`
  under the single-flight lock — only the firing caller pays; cache hits return at
  routers/thumbnails.py:180-182 before compute) and `worker/tasks.py:4646`
  (`patterns, _patterns_usage = …`, billed at worker/tasks.py:4652-4658). Tests/scripts updated;
  `chat/tools.py` does not call it. Zero-usage early return (empty ids) cannot write $0 ledger
  rows: the router 400s on empty `youtube_ids` (routers/thumbnails.py:223) and the worker guards
  `if patterns is None and youtube_ids:` (worker/tasks.py:4644).
- **Marker detection is exactly in sync with what was sent.** `dna_system_block` (util.py:58-61)
  attaches `{"type": "ephemeral", "ttl": "1h"}` when the measured Block1+Block2 prefix clears the
  1024-token floor; `has_1h_cache_marker` (util.py:64-78) checks `cache_control.ttl == "1h"` on
  the SAME `system` list object passed to the API call in all five features — titles.py:251→268,
  thumbnails.py:351→368, clip_titles.py:268→284, clip_captions.py:216→232,
  clip_explain.py:270→286. No re-measuring, no drift. Tested both ways in
  tests/test_knowledge_util.py:178-187. (Note: the helper scans only system blocks — correct
  today because no tool/message block carries a marker in any builder.)
- **Usage-dict keys consistent with billing extraction.** Producers emit
  `input_tokens/output_tokens/cache_read/cache_creation` (+ `cache_1h` bool);
  `record_llm_usage` (billing/ledger.py:203-212) reads exactly those four via `.get`, and
  `record_llm_metric` (observability.py:273-277) reads the same four in its dict branch — the
  extra bool key is inert in both. All five flagged-producer billing sites consume the flag as
  `cache_write_multiplier=2.0 if usage.get("cache_1h") else None`: routers/clips.py:1297/1397/1509
  and worker/tasks.py:4474/4698. The two `analyze_thumbnail_patterns` billing sites correctly
  omit the multiplier — that request sends no cache markers at all (no system prompt, no
  cache_control in content; thumbnails.py:137-174), so `cache_creation` is always 0 there.
  Multiplier math verified in billing/ledger.py:146-153 (None → 1.25× default, 2.0 for 1h).
- **Prompt-caching efficiency unchanged.** `dna_system_block` gating and block order untouched;
  `cache_1h` is written into the usage dict only after the response — zero bytes changed in any
  request. The `analyze_thumbnail_patterns` request is byte-identical to the pre-delta call.
- Tests green locally: 79 passed (tests/test_knowledge_util.py, tests/test_thumbnails.py incl.
  cache_1h True→2.0 / False→None billing assertions, tests/test_titles.py).

Load-bearing claims verified by reading, not assuming (2026-07-20 run; line refs may have
shifted by a few lines after 452a700 — re-verified still true 2026-07-29):
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

- [SEV2 / cross-module — fix belongs in chat/tools.py, not this slice] chat/tools.py:500
  `_suggest_clip_titles` calls this module's `generate_clip_title_suggestions` and discards
  `_usage` (`result, _usage = await …`) — the nested Sonnet call is never billed and is invisible
  to the Issue-290 spend guard. chat/runner.py:215-240 bills only the chat turn's OWN usage via
  `increment_usage`; the tool's inner API call's usage never reaches it. Same class as the
  intake gap fixed in this very commit (452a700, OFF_COURSE_BUGS 2026-07-29 row) but this call
  site was missed by the w2 billing audit; no DECISIONS/OFF_COURSE descope found. Bounded by
  chat rate limits (dollars small; spend-guard blindness is the real exposure). | fix: inside
  `_suggest_clip_titles`, after the call, `await record_llm_usage(creator_id, _usage,
  settings.COST_PER_MTOK_IN_SONNET, settings.COST_PER_MTOK_OUT_SONNET,
  cache_write_multiplier=2.0 if _usage.get("cache_1h") else None)` + a regression test mirroring
  tests/test_identity_chat.py::test_intake_turn_writes_billing_ledger_with_cache_tokens.
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
- [cleanup] (carry-forward, now doubly stale) thumbnails.py:329-330 docstring still says usage
  is "the token-count dict from ``stream_and_emit``" AND directs callers to
  ``billing.ledger.increment_usage`` — the actual billing path is ``record_llm_usage`` (which
  the cache_1h contract depends on). | fix: mirror the titles.py wording + name
  ``record_llm_usage``.
- [cleanup] (carry-forward) Bare unparameterized `-> tuple:` on titles.py:119 `_build_request`
  and thumbnails.py:216 `_build_concepts_request` (clip builders use
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
| 2 Concurrency & scale | ok — pause_turn loop bounded (max_rounds+1 calls, tested); builders async end-to-end; inputs bounded (images `[:10]`, transcript char caps); patterns compute still single-flight-locked with billing inside the compute path |
| 3 Security & compliance | 1 SEV2 (cross-module: chat/tools.py:500 discards the clip-titles usage dict → unbilled Sonnet call, spend-guard blind spot); within the slice ok — all 7 builders wrap every untrusted surface in the user turn; verbose full-prompt logging double-gated off in prod; no tokens/PII/SQL; honesty disclaimers Python-appended in every builder |
| 4 Clip-quality | n/a (generation module) — clip_explain still constrains `cited_principle` to the canonical enum |
| 5 Anthropic SDK | ok — floor-gated 1h cache marker on the 5 Sonnet builders, correctly none on the Haiku pair; NEW: `cache_1h` flag derived from the exact `system` blocks sent (no drift possible) so 1h writes bill 2× at every consumer; requests byte-identical to pre-delta (caching efficiency unchanged); usage summed across pause_turn rounds and logged + metered after every call |
| 6 Cleanliness & typing | 8 cleanup (all carried; thumbnails docstring now doubly stale — names `increment_usage` instead of the `record_llm_usage` path the cache_1h contract depends on); no TODO/print/pdb |
| 7 Error handling / API | n/a (not a router; typed SDK errors propagated to callers as documented) |
| 8 Config & paths | ok — models + web-search tool version config-driven and in .env.example; no filesystem paths; thumbnail URLs absolute https |

## Module verdict
clean (slice) with 1 cross-module SEV2 escalated — the w2/billing-audit delta is correct end to
end: both `analyze_thumbnail_patterns` callers handle the new tuple, the `cache_1h` flag is read
off the exact system blocks sent (marker ↔ multiplier cannot drift), usage keys match
billing/ledger extraction at all five consumer sites, and no request bytes changed (caching
efficiency intact). The one real defect found is in another slice: chat/tools.py:500 discards
the clip-titles usage dict, leaving that nested Sonnet call unbilled and spend-guard-invisible —
the same class of gap 452a700 fixed for intake. Route to the chat-module owner / issues triage.
