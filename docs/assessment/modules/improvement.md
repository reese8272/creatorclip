# improvement — assessed 2026-05-31

Wave-4 re-assessment. Wave 4 did NOT touch `improvement/`. This pass
re-verifies that the Wave-3 SEV1 closure (Fix A — streaming branch
forwards `tools=tools` to `stream_and_emit`) is still in place, that the
non-streaming branch still passes `tools=tools` correctly, and that none
of the four carried-over cleanups have regressed or been resolved. All
prior trace evidence still holds on the current source.

Slice covered:
- `improvement/__init__.py` (empty package marker; 1 line, intentionally blank)
- `improvement/brief.py`

## Findings

- [RESOLVED — Wave-3 Fix A still in place] improvement/brief.py:115-146 —
  streaming branch forwards `tools=tools` to `stream_and_emit` at
  brief.py:134. Re-verified end-to-end on the current source:
  (a) `_build_request` returns `(system, tools, messages)` at brief.py:92
  and the call site unpacks all three at brief.py:113;
  (b) `worker/anthropic_stream.stream_and_emit` accepts
  `tools: list | None = None` at worker/anthropic_stream.py:44 and
  conditionally forwards it to `client.messages.stream(**stream_kwargs)`
  only when not None (worker/anthropic_stream.py:75-76) — matching the
  older `dna/brief.py` no-tools call shape so that path stays untouched;
  (c) the non-streaming branch at brief.py:149-155 continues to pass
  `tools=tools` unchanged (brief.py:153);
  (d) the inline comment at brief.py:118-121 explicitly memorialises the
  Wave-3 fix ("without it, the brief loses web_search grounding —
  Wave-3 Fix A closed that SEV1"). Tombstone intact for future readers.
  Cache breakpoint on the static system prefix is identical across both
  call paths so any cache write on one path is readable on the other.

- [SEV2 — still open, blocked behind SDK bump] improvement/brief.py:136-142 —
  streaming log line bundles cache reads into a single `cached_read=`
  counter. The TTL-tier breakdown (5m vs 1h) requires
  `cache_creation.ephemeral_5m_input_tokens` /
  `ephemeral_1h_input_tokens` fields that only appear after the SDK bump
  (anthropic 0.40 → 0.105.2). Tracked under Issue-84 follow-ups per
  docs/DECISIONS.md:142-144. Rubric §5. | fix: when the SDK bump lands,
  switch on `getattr(usage, "cache_creation", None)` (the new structured
  breakdown object) and emit `cache_creation_5m=` / `cache_creation_1h=`
  alongside the totals. No code change in this issue.

- [cleanup — carried from Wave 2/3, not addressed] improvement/brief.py:55 —
  `_build_request(...) -> tuple` is a bare `tuple` annotation. The
  Issue-86 peer at `dna/brief.py` uses a parameterised shape; the matching
  shape here is `tuple[list[dict], list[dict], list[dict]]`. Rubric §6.
  | fix: replace the bare `tuple` with the parameterised shape so mypy
  catches shape drift if a future edit re-orders the return.

- [cleanup — carried from Wave 2/3, not addressed] improvement/brief.py:55,
  97 — `analytics: dict` (public signature) is un-parameterised while the
  local `payload: dict[str, object]` at brief.py:62 is. Rubric §6.
  | fix: annotate `analytics: dict[str, object]` (or a TypedDict matching
  the worker/tasks.py metrics shape) at both the public
  `generate_improvement_brief` signature and the `_build_request`
  signature so the call-graph types are consistent.

- [cleanup — carried from Wave 2/3, not addressed] improvement/brief.py:64 —
  `dna_brief[:1000]` is a magic number with a hand-waved trailing comment
  ("cap so system block stays cacheable"). Rubric §6. | fix: hoist to a
  module-level `_DNA_BRIEF_MAX_CHARS = 1000` so the rationale lives beside
  the constant.

- [cleanup — carried from Wave 3, not addressed] improvement/brief.py:5-7 —
  the docstring claim "The static prefix is below Sonnet 4.6's minimum
  cacheable size, so the cache does not engage for this low-frequency
  call" remains factually correct after Issue 84's audit (the floor is
  1024 tokens per docs/DECISIONS.md:156; the static `_SYSTEM_INSTRUCTIONS`
  block at brief.py:37-52 is ~822 chars / ~205 tokens, well under 1024).
  However the docstring does NOT name the figure, and the parallel
  descoping recommendation in DECISIONS.md:144 ("drop unproductive
  cache_control markers from improvement brief — < 1024-token Sonnet 4.6
  floor → 1.25× write premium for zero reads") is queued behind the SDK
  bump but applies to the very `cache_control: {"type": "ephemeral"}` at
  brief.py:76. Rubric §5/§6 (no behaviour bug; just a stale design that
  pays a 1.25× write premium for cache writes that will never be read).
  | fix: (a) optionally name the "1024 tokens" figure in the docstring
  for forward-readers; (b) when the SDK bump lands, drop the
  `cache_control` breakpoint at brief.py:76 entirely — tracked under
  Issue-84 follow-ups so explicitly out of scope for this issue, just
  flagging here so the next pass doesn't miss it.

## Trace results (Wave-4 re-verifications)

- **Wave 4 scope:** no commits touched `improvement/` or
  `worker/anthropic_stream.py` between baseline 67fddc9 and HEAD — the
  source matches the Wave-3-assessed state byte-for-byte. Re-verifications
  below are reads against the current file, confirming the Wave-3 trace
  evidence still holds.
- **SEV1 (streaming-path tools loss) remains CLOSED:**
  - call site: `improvement/brief.py:127-135` passes `tools=tools` to
    `stream_and_emit` (line 134) — confirmed by reading.
  - helper signature: `worker/anthropic_stream.py:44` accepts
    `tools: list | None = None`.
  - forward logic: `worker/anthropic_stream.py:69-76` adds `tools` to
    the `stream_kwargs` dict ONLY when not None, so the older
    `dna/brief.py` no-tools caller is unaffected (older SDK shapes
    raise on `tools=None`).
  - cache-key parity: `system[0]` at brief.py:68-77 is identical across
    both branches (same `_SYSTEM_INSTRUCTIONS` text + same `cache_control`
    marker), so any cache write on one path is readable on the other.
  - inline comment + docstring at brief.py:116-121 explicitly memorialise
    the fix so a future reader doesn't accidentally drop the kwarg.
- **Non-streaming path untouched and still passes tools:** confirmed at
  brief.py:149-155 — `client.messages.create(..., tools=tools, ...)` on
  line 153.
- **`_build_request` continues returning the 3-tuple:** confirmed at
  brief.py:92 and both call sites unpack it identically at brief.py:113.
- **Industry-standard check (Anthropic SDK), unchanged from Wave 3:**
  - streaming + tools combination: per the Anthropic SDK docs the
    `messages.stream(...)` context manager accepts the same kwargs as
    `messages.create(...)` including `tools`; tool-use blocks land on
    the same `content_block_*` event stream as text and the final
    message is reconstituted by `stream.get_final_message()` — which is
    exactly what `worker/anthropic_stream.py:78-91` does. The
    "FINAL text block is the answer" pattern (Issue 69) still applies
    to streaming because text_blocks ordering is preserved in
    `final.content`. Aligned with current best practice.
  - `web_search_20260209` remains the current GA tool string at
    `config.py` (Issue 84 bumped from the dated `20250305` preview).
  - prompt-caching breakpoint placement is still on the stable prefix
    (the static system block), with the volatile analytics block after
    the breakpoint — correct shape per the 2026 prompt-caching docs.
    Caveat: the static prefix is ~205 tokens so the breakpoint is
    inert until either (a) the prefix grows past 1024 tokens or
    (b) it is dropped per the Issue-84-follow-up (DECISIONS.md:144).
- **Honesty disclaimer Python-appended on BOTH paths:** confirmed —
  `_DISCLAIMER` appended at brief.py:146 (streaming return) and
  brief.py:171 (non-streaming return). Never delegated to the model.
- **Token logging fields match across paths:** both `logger.info` sites
  (brief.py:136-142 streaming; brief.py:157-163 non-streaming) emit the
  same `in / cached_read / cached_write / out` 4-field shape. The
  streaming side reads from the `usage` dict produced by
  `stream_and_emit` at worker/anthropic_stream.py:93-99; the
  non-streaming side reads `response.usage` directly with `getattr`
  defensiveness for the cache fields.
- **No PII / no token in log lines:** re-grep of `brief.py` confirms only
  two `logger.info` sites; both emit ONLY integer token counts. No
  channel title, no creator id, no prompt body, no analytics body.
- **Module-level singleton client preserved:** `_ANTHROPIC` constructed
  once at module scope (brief.py:24-28); both call paths use
  `.with_options(timeout=120.0)` which returns a derived view of the
  same underlying client (does not re-construct the HTTP pool).

## Security & compliance notes

- No OAuth tokens, no PII, no channel-identity secrets handled. Only an
  aggregate analytics dict + a capped DNA summary (`[:1000]`) reach the
  prompt. No `decrypt()` surface here.
- `logger.info` sites at brief.py:136-142 and :157-163 emit only integer
  token counts.
- Honesty constraint (CLAUDE.md): `_SYSTEM_INSTRUCTIONS` at brief.py:50
  ("never promise virality") + Python-appended `_DISCLAIMER` at
  brief.py:30-34 (does not promise virality or specific growth outcomes)
  — both honesty surfaces are intact on the streaming path because the
  same `system` and same trailing `_DISCLAIMER` are used.
- The worker upstream stores a safe error string in the DB row and the
  GET handler returns that safe string to the client. No stack trace /
  token / DB error surfaced. (Owned by worker/router agents; included
  only because the task brief asked for end-to-end honesty/logging
  verification.)

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — module-level singleton Anthropic client with timeout+retries; `.with_options(timeout=120.0)` returns a derived client; no DB/file/subprocess handles in this module |
| 2 Concurrency & scale | ok — both call paths run in Celery via `asyncio.to_thread` upstream (worker/anthropic_stream.py:13-15 documents the contract); no hidden blocking inside an `async def` here |
| 3 Security & compliance | ok — no tokens/PII in logs or prompt; capped DNA payload; isolation enforced upstream; no virality promise; honesty disclaimer always Python-appended on both branches |
| 4 Clip-quality | n/a (not a clip module) |
| 5 Anthropic SDK | Wave-3 SEV1 closure (tools forwarded on streaming path) STILL IN PLACE. 1 SEV2 open + informational (TTL-tier breakdown deferred behind SDK bump per DECISIONS.md:142-144). Cache breakpoint at end of stable prefix per 2026 standard; `web_search_20260209` is current GA tool version; max_tokens=2000; model/tool from config |
| 6 Cleanliness & typing | 4 cleanup — bare `tuple` return at :55, under-parameterised `analytics: dict` at :55/:97, magic `1000` at :64, optional docstring polish at :5-7. None regressed; none resolved by Wave 4 |
| 7 Error handling / API | n/a (not a router) — `RuntimeError` on empty response is appropriate for a Celery-task call path, mapped to a safe message upstream |
| 8 Config & paths | ok — `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `ANTHROPIC_WEB_SEARCH_TOOL` via `config.settings` (pydantic-settings, fail-fast); no paths in module |

## Module verdict
clean — Wave 4 did not touch the module; the Wave-3 Fix A closure of the
streaming-path `tools` SEV1 is still in place at brief.py:134, the
non-streaming branch still passes `tools=tools` at brief.py:153, and the
shared helper at `worker/anthropic_stream.py:44/75-76` continues to
accept and conditionally forward the kwarg without breaking the older
no-tools caller in `dna/brief.py`. Cache breakpoint, honesty disclaimer,
and 4-field token logging remain byte-identical across both call paths.
The one open SEV2 (TTL-tier breakdown) is correctly deferred to the
SDK-bump follow-up, and the four cleanups are non-blocking. No new
defects introduced or discovered this wave.
