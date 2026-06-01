# improvement — assessed 2026-05-31

Slice (read-only): `improvement/__init__.py` (empty), `improvement/brief.py`
(183 LOC, +12 since Wave-4 — all Wave-9/Issue-108 cleanup). Single public
entry point: `generate_improvement_brief()` — calls Claude with the
`web_search` tool over both a non-streaming `.create()` path and a streaming
`.stream()` path (via `worker.anthropic_stream.stream_and_emit`).

Last commit touching slice: `d6a7393` (Wave-9 cleanup sweep, 2026-05-31).
HEAD: 78630c6. The Wave-9 changes touched only the **cleanup-severity**
items flagged in the prior pass; the three SEV2s under Rubric §5 are
unchanged at the source level. Each finding below is re-traced against
the current file, not carried over by assumption.

## Findings

- [SEV2] improvement/brief.py:93 — `web_search` tool registered without
  `max_uses`, so a single brief request can issue an unbounded number of
  search calls inside one model turn. At hundreds of concurrent creators
  this is a real cost amplifier (each search round-trip is billed and
  adds to the 120s timeout budget). Rubric §2 + §5. **Status: OPEN since
  Wave-4** (re-verified — `tools` dict at line 93 has only `type` + `name`,
  no `max_uses` field; no `ANTHROPIC_WEB_SEARCH_MAX_USES` in `config.py`
  or `.env.example`). | fix: pass
  `"max_uses": settings.ANTHROPIC_WEB_SEARCH_MAX_USES` in the tool dict;
  add `ANTHROPIC_WEB_SEARCH_MAX_USES: int = 5` to `config.py` +
  `.env.example`. Same cap should be applied if other modules adopt
  web_search.

- [SEV2] improvement/brief.py:161-167 — `tool_choice` not set on either
  the `.create()` call (lines 161-167) or the streaming
  `stream_and_emit()` call (lines 139-147), so it defaults to `auto` and
  the model can skip web_search entirely. The brief's value proposition
  ("cite a CURRENT algorithm factor") is silently weakened when this
  happens — there is no programmatic guarantee that at least one search
  was performed. Rubric §5. **Status: OPEN since Wave-4.** | fix: pass
  `tool_choice={"type": "auto"}` explicitly OR
  `tool_choice={"type": "tool", "name": "web_search"}` to force at least
  one search before answering; add a regression test asserting the
  response contains at least one `tool_use` block under a recorded
  fixture. Plumb `tool_choice` through `worker/anthropic_stream.py` as a
  new `tool_choice: dict[str, Any] | None = None` kwarg so both paths
  carry it.

- [SEV2] improvement/brief.py:7-8, 58-60, 80-89 — docstring states "the
  static prefix is below Sonnet 4.6's minimum cacheable size, so the
  cache does not engage for this low-frequency call." The
  `cache_control: {"type": "ephemeral"}` marker at line 88 is therefore
  a 1.25× write premium for cache writes that will never be read (per
  `docs/DECISIONS.md:933` — follow-up queued behind the SDK bump). Rubric
  §5 (caching mandatory). **Status: OPEN since Wave-4.** Wave-9 named
  the related magic number (`_DNA_BRIEF_MAX_CHARS = 1000`) and updated
  the comment to reference the 1024-token Sonnet 4.6 floor, but did not
  pad the prefix or drop the marker — so the inert cache write is still
  being paid on every call. | fix: either pad `_SYSTEM_INSTRUCTIONS`
  past the ~1024-token Sonnet 4.6 floor (mirror the longer preamble in
  `dna/brief.py`), OR drop the `cache_control` marker entirely until the
  prefix grows. Whichever lands, close the row in `docs/DECISIONS.md:933`.

- [SEV2 — deferred behind SDK bump] improvement/brief.py:148-154,
  169-175 — both `logger.info` sites bundle cache reads/writes into
  single `cached_read=` / `cached_write=` counters. The 5m vs 1h TTL-tier
  breakdown requires the structured `usage.cache_creation` object that
  only ships with the SDK bump (anthropic 0.40 → 0.105+, tracked under
  Issue-84 follow-ups per `docs/DECISIONS.md:142-144`). Rubric §5.
  **Status: OPEN — still deferred.** | fix: when the SDK bump lands,
  read `getattr(usage, "cache_creation", None)` and emit
  `cache_creation_5m=` / `cache_creation_1h=` alongside totals. No code
  change in this pass.

- [cleanup] improvement/brief.py:67 — `_build_request(...) -> tuple`
  return annotation remains bare/unparameterised. Wave-9 explicitly
  noted (commit d6a7393 + inline comment block) that the parameterised
  form using SDK TypedDict params was incompatible with the Anthropic
  SDK 0.40 stubs and was deliberately kept as a bare `tuple`. The peer
  `dna/brief.py::_build_request` is in the same state. Rubric §6.
  **Status: DEFERRED behind SDK bump (Issue 84) — justification on disk.**
  | fix (when SDK bumps): re-attempt parameterisation as
  `tuple[list[dict], list[dict], list[dict]]` (concrete `dict`, not the
  SDK TypedDict); this keeps mypy honest about the 3-tuple shape without
  colliding with the SDK stubs. If even that breaks the streaming call
  path (it should not — `stream_and_emit` types `system: Any` +
  `messages: list[dict[str, Any]]`), defer cleanly to the SDK bump.

- [cleanup] improvement/brief.py:134 — `from worker.anthropic_stream
  import stream_and_emit` is still an in-function import without a "why"
  comment. The local import IS load-bearing (avoids a circular import
  with `worker.tasks`, which imports `improvement.brief`) — verified by
  reading. Rubric §6. **Status: OPEN since Wave-4** (Wave-9 sweep
  missed this one). | fix: add a one-line comment
  `# Local import: avoids circular dependency with worker.tasks`.

## Wave-9 closures (re-verified against current source)

- [closed] improvement/brief.py:65, 109 — `analytics: dict` → now
  `analytics: Mapping[str, object]` on both `_build_request` and
  `generate_improvement_brief`. **Verified:** covariant `Mapping` is
  the correct choice — it accepts the worker caller's concrete
  `dict[str, object]` without forcing invariance upstream. The Wave-9
  commit message explicitly cites the 5 mid-sweep invariance issues
  resolved this way. Closes the prior Rubric §6 cleanup.

- [closed] improvement/brief.py:60, 76 — magic `1000` named as
  module-scope `_DNA_BRIEF_MAX_CHARS` with a docstring pointing back
  to the inert-cache claim and the 1024-token Sonnet 4.6 floor.
  **Verified:** single source of truth, consumed at line 76. Closes
  the prior Rubric §6 cleanup.

## Net-new findings vs Wave-4

None. Wave-9 (Issue 108) is a pure cleanup sweep — closed 2 of 3 prior
cleanup items, justifiably deferred 1 behind the SDK bump (with on-disk
justification + an explicit inline comment at line 85-87 calling out
the SDK TypedDict incompatibility), introduced no new SEV findings, and
did not regress any rubric category. The three open SEV2s under Rubric
§5 are unchanged at the source level and remain the single
highest-leverage cluster for this module.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — `_ANTHROPIC` is a module-level singleton (brief.py:25-29) with timeout + 2 retries; `.with_options(timeout=120.0)` returns a derived view of the same client, no per-call HTTP-pool rebuild. No DB sessions / file handles / subprocesses in this slice. |
| 2 Concurrency & scale | 1 SEV2 (unbounded web_search fan-out, line 93). Both call paths run under `asyncio.to_thread` in the worker (per `worker/anthropic_stream.py:13-15` contract); no hidden blocking inside an `async def` in this module. |
| 3 Security & compliance | ok — honesty disclaimer enforced in Python at brief.py:31-35 and appended on BOTH paths (line 158 streaming + line 183 non-streaming), never delegated to the model. Two `logger.info` sites emit ONLY integer token counts — no channel title, no creator id, no prompt body, no analytics body. No OAuth tokens / PII in scope. `_SYSTEM_INSTRUCTIONS` explicitly says "never promise virality." No raw SQL. |
| 4 Clip-quality | n/a — not a clip module. |
| 5 Anthropic SDK | 3 SEV2 OPEN (no `max_uses`, no `tool_choice`, cache breakpoint is inert at current prefix size) + 1 SEV2 deferred behind SDK bump (TTL-tier token breakdown). Token logging IS present on both paths (4-field `in/cached_read/cached_write/out`). `max_tokens=2000` set on both paths. `web_search_20260209` is current GA tool string. Cache breakpoint correctly placed at end of static prefix per 2026 prompt-caching pattern — issue is only that the prefix is below the cacheable floor. The Wave-3 SEV1 (streaming path dropped `tools`) remains CLOSED at brief.py:146 — re-verified end-to-end against `worker/anthropic_stream.py:44, 69-76`. |
| 6 Cleanliness & typing | 2 cleanup OPEN (bare `tuple` return — justified-deferred behind SDK bump; undocumented in-function import — Wave-9 missed). 2 prior cleanups CLOSED in Wave-9 (`analytics` → `Mapping[str, object]`; magic `1000` → `_DNA_BRIEF_MAX_CHARS`). No regressions. |
| 7 Error handling / API | n/a — not a router. `RuntimeError` on empty Claude response is appropriate for a Celery-task call path and is mapped to a safe message by the upstream worker. |
| 8 Config & paths | ok — `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `ANTHROPIC_WEB_SEARCH_TOOL` all flow through `config.settings` (pydantic-settings, fail-fast). No paths in this module. The two open SEV2 fixes above would add one new setting (`ANTHROPIC_WEB_SEARCH_MAX_USES`) to `.env.example`; not present yet. |

## Module verdict

NEEDS-WORK — module is small, well-isolated, security-clean, and the
Wave-3 streaming-path `tools` SEV1 closure is still in place. Wave-9
landed the cheap typing/naming cleanup (`Mapping[str, object]`,
`_DNA_BRIEF_MAX_CHARS`) and justified the deferred-typing cleanup on
disk. Open work is all Rubric §5 (Anthropic SDK): `web_search` runs
without `max_uses` or `tool_choice`, and the cache breakpoint is still
documented-inert (1.25× write premium for unread writes). None are
BLOCKERs; all are real cost/grounding defects that compound at
hundreds-of-creators scale.
