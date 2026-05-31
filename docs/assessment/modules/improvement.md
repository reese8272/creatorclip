# improvement — assessed 2026-05-31

Slice (read-only): `improvement/__init__.py` (empty), `improvement/brief.py`
(171 LOC). Single public entry point: `generate_improvement_brief()` — calls
Claude with the `web_search` tool over both a non-streaming `.create()` path
and a streaming `.stream()` path (via `worker.anthropic_stream.stream_and_emit`).

Source has not changed since the Wave-4 pass (last commit touching
`improvement/` is 04ca3da, "Wave 3 hotfix batch" — HEAD is 78630c6). All
findings below are re-traced against the current file, not carried over by
assumption.

## Findings

- [SEV2] improvement/brief.py:81 — `web_search` tool registered without
  `max_uses`, so a single brief request can issue an unbounded number of
  search calls inside one model turn. At hundreds of concurrent creators
  this is a real cost amplifier (each search round-trip is billed and adds
  to the 120s timeout budget). Rubric §2 + §5. | fix: pass
  `"max_uses": settings.ANTHROPIC_WEB_SEARCH_MAX_USES` in the tool dict,
  add `ANTHROPIC_WEB_SEARCH_MAX_USES: int = 5` to `config.py` +
  `.env.example`. Same cap should be applied if other modules use
  web_search.

- [SEV2] improvement/brief.py:81 — `tool_choice` not set, so it defaults to
  `auto` and the model can skip web_search entirely. The brief's value
  proposition (cite a CURRENT algorithm factor) is silently weakened when
  this happens — there is no programmatic guarantee that at least one
  search was performed. Rubric §5. | fix: pass
  `tool_choice={"type": "auto"}` explicitly OR
  `tool_choice={"type": "tool", "name": "web_search"}` to force at least
  one search before answering; add a regression test asserting the
  response contains at least one `tool_use` block under a recorded fixture.

- [SEV2] improvement/brief.py:5-7, 76 — docstring states "the static prefix
  is below Sonnet 4.6's minimum cacheable size, so the cache does not
  engage for this low-frequency call." The `cache_control: {"type":
  "ephemeral"}` marker at line 76 is therefore a 1.25× write premium for
  cache writes that will never be read (per `docs/DECISIONS.md:144` —
  follow-up queued behind the SDK bump). Rubric §5 (caching mandatory).
  | fix: either pad `_SYSTEM_INSTRUCTIONS` past the ~1024-token Sonnet 4.6
  floor (mirror the longer preamble in `dna/brief.py`), OR drop the
  `cache_control` marker entirely. Whichever lands, name the "1024 tokens"
  figure in the docstring so the next reader can verify the claim.

- [SEV2 — deferred behind SDK bump] improvement/brief.py:136-142,
  157-163 — both `logger.info` sites bundle cache reads/writes into single
  `cached_read=` / `cached_write=` counters. The 5m vs 1h TTL-tier
  breakdown requires the structured `usage.cache_creation` object that
  only ships with the SDK bump (anthropic 0.40 → 0.105+, tracked under
  Issue-84 follow-ups per `docs/DECISIONS.md:142-144`). Rubric §5. | fix:
  when the SDK bump lands, read `getattr(usage, "cache_creation", None)`
  and emit `cache_creation_5m=` / `cache_creation_1h=` alongside totals.
  No code change in this pass.

- [cleanup] improvement/brief.py:55 — `_build_request(...) -> tuple` has a
  bare unparameterised `tuple` return annotation. The peer
  `dna/brief.py::_build_request` uses a parameterised shape. Rubric §6.
  | fix: annotate `-> tuple[list[dict], list[dict], list[dict]]` so mypy
  catches shape drift if the return is re-ordered.

- [cleanup] improvement/brief.py:55, 97 — public `analytics: dict` is
  unparameterised while the local `payload: dict[str, object]` at :62 is.
  Rubric §6. | fix: annotate both `_build_request` and
  `generate_improvement_brief` as `analytics: dict[str, object]` (or a
  `TypedDict` matching the worker/tasks.py metrics shape).

- [cleanup] improvement/brief.py:64 — `dna_brief[:1000]` is a magic
  number. The trailing comment ("cap so system block stays cacheable")
  also references the cache claim that the SEV2 above contradicts.
  Rubric §6. | fix: hoist to `_DNA_BRIEF_MAX_CHARS = 1000` at module
  scope; if the cache breakpoint is dropped per the SEV2 above, update
  the comment to the real reason (prompt-token cap).

- [cleanup] improvement/brief.py:122 — `from worker.anthropic_stream import
  stream_and_emit` is imported inside the function body without a "why"
  comment. The local import IS load-bearing (avoids a circular import
  with `worker.tasks`, which imports `improvement.brief`) — verified by
  reading. Rubric §6. | fix: add a one-line comment
  `# Local import: avoids circular dependency with worker.tasks`.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — `_ANTHROPIC` is a module-level singleton (brief.py:24-28) with timeout + 2 retries; `.with_options(timeout=120.0)` returns a derived view of the same client, no per-call HTTP-pool rebuild. No DB sessions / file handles / subprocesses in this slice. |
| 2 Concurrency & scale | 1 SEV2 (unbounded web_search fan-out). Both call paths run under `asyncio.to_thread` in the worker (per `worker/anthropic_stream.py:13-15` contract); no hidden blocking inside an `async def` in this module. |
| 3 Security & compliance | ok — honesty disclaimer enforced in Python at brief.py:30-34 and appended on BOTH paths (line 146 streaming + line 171 non-streaming), never delegated to the model. Two `logger.info` sites emit ONLY integer token counts — no channel title, no creator id, no prompt body, no analytics body. No OAuth tokens / PII in scope. `_SYSTEM_INSTRUCTIONS` explicitly says "never promise virality." No raw SQL. |
| 4 Clip-quality | n/a — not a clip module. |
| 5 Anthropic SDK | 3 SEV2 open (no `max_uses`, no `tool_choice`, cache breakpoint is inert at current prefix size). Token logging IS present on both paths (4-field `in/cached_read/cached_write/out`). `max_tokens=2000` set on both paths. `web_search_20260209` is current GA tool string. Cache breakpoint correctly placed at end of static prefix per 2026 prompt-caching pattern — issue is only that the prefix is below the cacheable floor. The Wave-3 SEV1 (streaming path dropped `tools`) remains CLOSED at brief.py:134 — re-verified end-to-end against `worker/anthropic_stream.py:44, 69-76`. |
| 6 Cleanliness & typing | 4 cleanup (bare `tuple`, unparameterised `dict`, magic `1000`, undocumented in-function import). None regressed since Wave 4; none resolved. |
| 7 Error handling / API | n/a — not a router. `RuntimeError` on empty Claude response is appropriate for a Celery-task call path and is mapped to a safe message by the upstream worker. |
| 8 Config & paths | ok — `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `ANTHROPIC_WEB_SEARCH_TOOL` all flow through `config.settings` (pydantic-settings, fail-fast). No paths in this module. The two SEV2 fixes above would add one new setting (`ANTHROPIC_WEB_SEARCH_MAX_USES`) to `.env.example`. |

## Module verdict

NEEDS-WORK — module is small, well-isolated, security-clean, and the
Wave-3 streaming-path `tools` SEV1 closure is still in place. Open work is
all rubric §5 (Anthropic SDK): caching is documented as inert (1.25× write
premium for unread writes), `web_search` runs without `max_uses` or
`tool_choice`, and the TTL-tier token breakdown is queued behind the SDK
bump. None are BLOCKERs; all are real cost/grounding defects that
compound at hundreds-of-creators scale.
