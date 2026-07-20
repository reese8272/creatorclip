# knowledge — assessed 2026-07-20

Slice: `knowledge/{chapters,clip_captions,clip_explain,clip_titles,hooks,thumbnails,titles,util}.py`, `knowledge/__init__.py` (empty).
Prior run: 2026-07-01. Diff scrutinized: `git diff f70a857..HEAD -- knowledge/` (all 8 source files
changed — Issue 82a sync→async migration, Issue 352 untrusted-content relocation, Issues 315/352
measured-floor cache gating via new `knowledge/util.py:dna_system_block`, Issue 350-adjacent
pause_turn loop in thumbnails).

Load-bearing claims verified by reading, not assuming:
- `worker/celery_app.py:112-133` — `run_async` executes on a per-worker-process singleton loop
  installed at `worker_process_init`; the module-level `AsyncAnthropic` clients therefore bind their
  httpx pool to one persistent loop per process (no dead-loop rebind under prefork). The "prefork-safe
  lazy pool bind" comments hold.
- All call sites now `await` the async builders (`routers/clips.py:1169/1267/1373`, `chat/tools.py:500`,
  `worker/tasks.py` via `run_async`, `routers/thumbnails.py:233` passes the coroutine into the
  single-flight cache) — no stale `asyncio.to_thread(builder, …)` wrapper remains.
- `verbose.vlog_llm_request` (full-prompt logging incl. DNA/transcripts) is double-gated
  (`VERBOSE_LOGGING` + `VERBOSE_LOGGING_ALLOW_PROD` in production, config.py:525-532).
- Floor-gated cache marker is unit-tested (`tests/test_knowledge_util.py:152-164`,
  `tests/test_llm_conformance.py`).

## Resolved since 2026-07-01
- **[SEV2 → FIXED] Inert 1h cache marker below the 1,024-token floor for empty DNA briefs.**
  `knowledge/util.py:39-61` adds `dna_system_block(static_text, dna_text)` which attaches
  `cache_control {ttl:"1h"}` only when `(chars // 4) >= 1024` — the Issue-315 pattern from
  clip_engine/scoring.py, now adopted in titles.py:134, thumbnails.py:225, clip_titles.py:157,
  clip_captions.py:129, clip_explain.py:158. hooks.py:190-201 and chapters.py:186-194 correctly
  carry NO marker (Haiku 4.5's 4,096-token floor, per the Issue-135 audit comments). The misleading
  "~1,550 tokens clears the floor" comments were corrected everywhere.
- **[SEV2 → FIXED] Untrusted transcript/title text in the SYSTEM role in the older builders.**
  Issue 352: titles.py:155-161 now moves `video_title` + `transcript_summary` to the user turn via
  `wrap_untrusted`; thumbnails.py:248-252 moves `transcript_hook`; hooks.py:224-228 moves
  `transcript_excerpt`. System Block 3 in each now carries only trusted computed/factual context
  (channel name, retention stats, pattern lines). Posture now matches the clip builders.

## Findings

- [SEV2] titles.py:239 + hooks.py:243 — the two web-search streaming builders call
  `stream_and_emit`, which has **no `pause_turn` continuation**: it returns
  `text_blocks[-1].text` of whatever turn the server paused (or raises "Claude returned no
  text block"), so a long web-search turn that pauses yields partial/empty JSON →
  `parse_candidates` / `parse_hook_report` raise ValueError and the task fails. The codebase
  treats pause_turn as a real live behavior everywhere else: thumbnails.py:336-356 got an
  inline 5-round loop this cycle, improvement/brief.py handles it (Issue 350,
  tests/test_brief_caching.py:268), and the chat runner resumes it (tests/test_chat.py:156).
  titles/hooks are the only remaining web-search callers without the loop. | fix: extract the
  thumbnails loop into `worker/anthropic_stream.py` as e.g.
  `stream_until_final(client, task_id, *, max_rounds=5, **kwargs)` (append
  `{"role":"assistant","content":msg.content}` and re-call while `stop_reason == "pause_turn"`,
  summing usage) and use it from titles, hooks, AND thumbnails — which also removes the
  function-local `_MAX_SEARCH_ROUNDS`/agentic loop from thumbnails.py:323-356 (DRY).
- [SEV2] chapters.py:195-203 — raw transcript segment text is interpolated into the user
  message with no `wrap_untrusted`, and chapters' `_SYSTEM_INSTRUCTIONS` (lines 36-59) is the
  only builder prompt that does NOT include `UNTRUSTED_CONTENT_POLICY` — making the
  util.py:15 comment ("All nine builders import and prepend this string") inaccurate. Bounded
  blast radius (output is validated, titles truncated to 40 chars, content sits in the correct
  user role), but the module's own injection posture is violated by its one unwrapped
  transcript surface. | fix: prepend `UNTRUSTED_CONTENT_POLICY` to chapters'
  `_SYSTEM_INSTRUCTIONS` (~230 tokens, still far below Haiku's 4,096 floor so no caching
  implication) and wrap the joined segment block as
  `wrap_untrusted("video_transcript_segments", "\n".join(segment_lines))`; update the util.py
  comment.
- [cleanup] (carry-forward) 7 separate module-level `AsyncAnthropic` clients —
  chapters.py:25, clip_captions.py:39, clip_explain.py:42, clip_titles.py:37, hooks.py:32,
  thumbnails.py:38, titles.py:42 — the sync→async migration preserved the 7-pools-for-one-provider
  duplication. | fix: one shared `AsyncAnthropic` in `knowledge/_client.py`; keep per-call
  `.with_options(timeout=…)` (shares the underlying pool).
- [cleanup] (carry-forward) Identical `usage_dict` construction duplicated at
  clip_titles.py:276-281, clip_captions.py:224-229, clip_explain.py:278-283 (same 4-key
  `getattr(response.usage, …) or 0` block; `worker/anthropic_stream.py:113/186` builds the same
  shape a 4th/5th time). | fix: `usage_from_response(usage) -> dict[str, int]` in knowledge/util.py
  (or observability) used by all.
- [cleanup] (carry-forward) The per-call `logger.info("… tokens: in=%d cached_read=%d
  cached_write=%d out=%d", …)` block is still duplicated in all 7 builders (chapters.py:225,
  clip_captions.py:230, clip_explain.py:284, clip_titles.py:282, hooks.py:255, thumbnails.py:366,
  titles.py:251). | fix: fold into `record_llm_metric` / `record_llm_tokens`.
- [cleanup] (carry-forward) Bare unparameterized `-> tuple:` on titles.py:117 `_build_request`
  and thumbnails.py:199 `_build_concepts_request` (clip builders use
  `-> tuple[list[dict], list[dict]]`). | fix: `-> tuple[list[dict], list[dict], list[dict]]`.
- [cleanup / needs-runtime-confirmation] (carry-forward) `ANTHROPIC_WEB_SEARCH_TOOL` still pins
  `web_search_20260209` (config.py:131) with `allowed_callers=["direct"]` forced at titles.py:143,
  thumbnails.py:236, hooks.py:214 to suppress dynamic filtering. Works per the 2026-07-01 live-doc
  check; a live smoke asserting a non-empty final text block remains the guard against a routing
  regression on this version.
- [cleanup] hooks.py:181-186 — inside the `retention_drop_at_s is not None` branch,
  `f"video at {retention_at_drop:.1%}"` raises TypeError if `retention_at_drop` is None while the
  sibling values are `or 0`-guarded on the same lines. The pair always co-varies when it comes from
  `compute_retention_drop` (returns both-None or both-set), so latent only — but the signature
  permits the mismatch. | fix: `{(retention_at_drop or 0):.1%}` to match the neighbors.

Note (not a finding): `dna_system_block` measures only Block 1 + Block 2 chars, but for the
web-search builders the tool definition also precedes the breakpoint in the cacheable prefix
(tools → system ordering). The error is in the conservative direction (marker occasionally
omitted just above the true floor), never an inert marker — acceptable per the Issue-315 pattern.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | 1 cleanup (7 duplicate AsyncAnthropic clients, carried); loop binding of the async clients verified against the worker's singleton loop; no DB/temp-media here |
| 2 Concurrency & scale | 1 SEV2 (no pause_turn continuation in titles/hooks streaming paths); Issue 82a removed the to_thread hops — builders are truly async end-to-end; inputs bounded (images `[:10]`, transcript char caps, search rounds capped where the loop exists) |
| 3 Security & compliance | 1 SEV2 (chapters: unwrapped transcript + missing UNTRUSTED_CONTENT_POLICY); both prior system-role SEV2s fixed (Issue 352); verbose full-prompt logging double-gated off in prod; no tokens/PII/SQL in this module; honesty disclaimers Python-appended in every builder |
| 4 Clip-quality | n/a (generation module) — clip_explain still constrains `cited_principle` to the canonical enum at the API layer + parse-time |
| 5 Anthropic SDK | ok — prior inert-marker SEV2 fixed via floor-gated `dna_system_block` (tested); usage logged + metered after every call incl. the vision call; structured outputs (`additionalProperties: false`) on non-web-search paths only (would 400 with citations); Haiku paths correctly uncached below the 4,096 floor |
| 6 Cleanliness & typing | 5 cleanup (4 carried DRY/typing + hooks None-format); no TODO/print/pdb |
| 7 Error handling / API | n/a (not a router; typed SDK errors propagated to callers as documented) |
| 8 Config & paths | ok — models + web-search tool version config-driven and described in .env.example; no filesystem paths; thumbnail URLs absolute https |

## Module verdict
NEEDS-WORK — both 2026-07-01 SEV2s are verifiably fixed (floor-gated cache marker + untrusted
content relocated to the user turn), and the async migration is sound; two SEV2s remain:
titles/hooks web-search streams lack the pause_turn continuation the rest of the codebase already
implements, and chapters is the one builder with an unwrapped transcript and no untrusted-content
policy block. Plus carried DRY cleanups (7 clients, duplicated usage/log blocks).
