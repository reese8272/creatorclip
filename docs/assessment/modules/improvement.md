# improvement — assessed 2026-05-31

Wave-2 re-assessment. Wave-1 verdict was clean. Wave 2 (Issue 92) refactored
`improvement/brief.py` to extract `_build_request(channel_title, analytics,
dna_brief) -> (system, tools, messages)` and added a `task_id: str | None`
kwarg that routes to `worker.anthropic_stream.stream_and_emit` — the same
pattern Issue 86 introduced in `dna/brief.py`. Obsolete
`# type: ignore[...]` comments were removed; mypy 1.14.1 confirms the local
`list[dict]` typing no longer needs them. Slice covered:
`improvement/__init__.py` (empty package marker, 0 lines),
`improvement/brief.py`.

The re-verification surfaced ONE new SEV1 introduced by the streaming-path
extraction, plus the same low-severity cleanups as Wave 1.

## Findings

- [SEV1] improvement/brief.py:124-131 — Wave-2 streaming branch silently
  drops the `tools=[web_search]` argument that the non-streaming branch
  forwards at :149. `_build_request` returns `(system, tools, messages)`
  and `tools` is unpacked at :113, but the streaming call site never
  forwards it to `stream_and_emit`, and `stream_and_emit` itself does not
  accept a `tools` kwarg (worker/anthropic_stream.py:36-44 signature is
  `client, task_id, *, model, max_tokens, system, messages`). Net effect:
  any caller passing `task_id=...` gets a brief generated WITHOUT
  web_search grounding — silently neutering the module's stated purpose
  ("synthesise data + live-research grounded recommendations", brief.py
  docstring + `_SYSTEM_INSTRUCTIONS`:43-45). This also breaks the docstring
  claim at :117-118 ("Same prompt structure as the .create() path so cache
  breakpoints are interchangeable") — the structures diverge by exactly
  the tool that defines the cache prefix's downstream behaviour. Rubric §5
  (Anthropic SDK — web-search tool used where live research is intended)
  and §6 (DRY / consistency between the two paths). Note: `dna/brief.py`
  does not have this bug because it never used `tools` — the streaming
  helper was designed for that simpler call shape. `improvement/brief.py`
  is the first caller that needs a tool through the streaming wrapper.
  | fix: (a) extend `worker/anthropic_stream.stream_and_emit` to accept
  `tools: list | None = None` and forward to `client.messages.stream(...)`
  when not None; (b) at improvement/brief.py:124-131 pass `tools=tools`.
  Add a regression test that mocks `stream_and_emit` and asserts it
  receives `tools=[{"type": "web_search_20260209", "name": "web_search"}]`
  whenever `task_id` is supplied. Refresh the comment at brief.py:139-141
  to reflect that web_search IS now wired through the stream while the
  LAST text block remains the synthesised answer (Issue 69 pattern).

- [SEV2] improvement/brief.py:132-138 — streaming log line still bundles
  cache reads into a single `cached_read=` counter. The task brief
  acknowledges TTL-tier breakdown (5m vs 1h) is deferred to the
  Issue-84-follow-up SDK bump, so this is informational, not a Phase-4
  blocker — but flagging here so it's not lost: once the SDK exposes
  `cache_creation.ephemeral_5m_input_tokens` /
  `cache_creation.ephemeral_1h_input_tokens`, this log line will lose
  the breakdown silently. Rubric §5. | fix: when Issue 84's SDK bump
  lands, branch on `getattr(usage, "cache_creation", None)` (structured
  breakdown object) and emit `cache_creation_5m=` / `cache_creation_1h=`
  alongside totals. No code change in this issue.

- [cleanup] improvement/brief.py:55 — `_build_request(...) -> tuple` is a
  bare `tuple` annotation. The Issue-86 peer at `dna/brief.py:62` uses
  the precise `tuple[list[dict], list[dict]]`. The new helper here
  returns three lists, so the matching shape would be
  `tuple[list[dict], list[dict], list[dict]]`. Rubric §6. | fix: replace
  the bare `tuple` with the parameterised shape so mypy can catch shape
  drift if a future edit re-orders the return.

- [cleanup] improvement/brief.py:55, 97 — `analytics: dict` (public
  signature) is un-parameterised while the local `payload: dict[str,
  object]` at :62 is. Same finding as Wave 1 — Wave 2 did not address
  it. Rubric §6. | fix: annotate `analytics: dict[str, object]` (or a
  TypedDict matching `worker/tasks.py`'s metrics shape) at both the
  public `generate_improvement_brief` signature and the `_build_request`
  signature so both call sites are consistent.

- [cleanup] improvement/brief.py:64 — `dna_brief[:1000]` is a magic
  number with a hand-waved justification in the trailing comment ("cap
  so system block stays cacheable"). Rubric §6. | fix: hoist to a
  module-level `_DNA_BRIEF_MAX_CHARS = 1000` so the rationale lives
  beside the constant.

## Trace results (Wave-2-specific re-verifications)

- **Streaming path uses the same `_build_request` shape:** confirmed —
  both branches unpack `system, tools, messages = _build_request(...)`
  at :113 once, so the cache breakpoint embedded in `system[0]` (the
  static `_SYSTEM_INSTRUCTIONS` block carrying `cache_control:
  {"type": "ephemeral"}` at brief.py:69-77) is byte-identical across
  both call paths. A streaming call CAN benefit from a prior
  non-streaming call's cache write (and vice versa) at the system-block
  level — that part of the docstring claim holds. The `tools` shape
  divergence flagged above does NOT affect the cache-key on the system
  prefix, only the downstream tool-use behaviour.
- **`task_id` kwarg behaviour matches `dna/brief.py::generate_brief`'s
  Issue-86 pattern:** confirmed structurally — both modules: (i) build
  the request shape once via the extracted helper, (ii) branch on
  `task_id is not None`, (iii) import `stream_and_emit` lazily inside
  the branch (avoids a top-level worker dep when only the .create path
  is used), (iv) log a 4-field token summary keyed
  `in/cached_read/cached_write/out`, (v) append `_DISCLAIMER`. The only
  intentional shape difference is `improvement/brief.py:123` passing
  `client = _ANTHROPIC.with_options(timeout=120.0)` because the
  improvement brief's underlying call (when not streaming) needs the
  longer timeout for web_search — fine, but as noted in the SEV1, the
  streaming branch ALSO loses the tool that was the reason for that
  longer timeout in the first place.
- **Honesty disclaimer still Python-appended on BOTH paths:** confirmed
  — `_DISCLAIMER` appended at brief.py:142 (streaming) and brief.py:167
  (non-streaming). Never delegated to the model.
- **Token logging captures input / cache_read / cache_creation /
  output:** confirmed on both paths. Streaming path reads from the
  `usage` dict returned by `stream_and_emit` (worker/anthropic_stream.py
  :78-83); non-streaming reads directly from `response.usage` with
  `getattr` defensiveness for the cache fields. TTL-tier breakdown
  missing is a known Issue-84 follow-up.
- **No PII / no token in log lines:** grep of brief.py confirms only
  two `logger.info` sites; both emit ONLY integer token counts. No
  channel title, no creator id, no prompt body, no analytics body.
- **Removed `type: ignore` comments verified safe:** the system block
  list is now typed `list[dict]` (brief.py:68), not the SDK's
  `TextBlockParam`, so the `cache_control` key the SDK accepts at
  runtime no longer trips a typed-dict stub complaint. Mypy 1.14.1
  agrees the ignores were dead — confirmed by reading the surrounding
  comment at brief.py:74-77 explaining the rationale.
- **Industry standard check (Anthropic SDK):** `web_search_20260209`
  configured at config.py:56 — current GA tool version per Wave-2
  bump in Issue 84, with dynamic filtering. Cache breakpoint at end
  of stable prefix per the 2026 prompt-caching standard. Single
  4-byte field token logging is correct for the current SDK; the
  TTL-tier follow-up is captured.

## Security & compliance notes

- No OAuth tokens, no PII, no channel-identity secrets handled. Only an
  aggregate analytics dict + a capped DNA summary (`[:1000]`) reach the
  prompt. No `decrypt()` surface here.
- The `logger.info` sites at brief.py:132-138 and :153-159 emit only
  integer token counts.
- The worker upstream stores a safe error string in the DB row and logs
  the exception server-side; the GET handler returns the safe string to
  the client. No stack trace / token / DB error surfaced. (Owned by
  worker/router agents; included only because the task brief asked for
  end-to-end honesty/logging verification.)

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — module-level singleton Anthropic client with timeout+retries; `.with_options(timeout=120.0)` returns a derived client; no DB/file/subprocess handles in this module |
| 2 Concurrency & scale | ok — both call paths run in Celery via `asyncio.to_thread` upstream (worker/anthropic_stream.py:13-15 documents the contract); no hidden blocking inside an `async def` here |
| 3 Security & compliance | ok — no tokens/PII in logs or prompt; capped DNA payload; isolation enforced upstream; no virality promise; honesty disclaimer always Python-appended |
| 4 Clip-quality | n/a (not a clip module) |
| 5 Anthropic SDK | 1 SEV1 (streaming path drops `tools=[web_search]`) + 1 SEV2 (TTL-tier breakdown deferred to Issue 84). Cache breakpoint at end of stable prefix per 2026 standard; `web_search_20260209` is current GA tool version; max_tokens=2000; model/tool from config |
| 6 Cleanliness & typing | 3 cleanup — bare `tuple` return at :55, under-parameterised `analytics: dict` at :55/:97, magic `1000` at :64. The Wave-2 removal of obsolete `# type: ignore[...]` comments verified safe under mypy 1.14.1 |
| 7 Error handling / API | n/a (not a router) — `RuntimeError` on empty response is appropriate for a Celery-task call path, mapped to a safe message upstream |
| 8 Config & paths | ok — `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `ANTHROPIC_WEB_SEARCH_TOOL` via `config.settings` (pydantic-settings, fail-fast); no paths in module |

## Module verdict
NEEDS-WORK — Wave-2's extraction of `_build_request` is structurally
clean and matches the Issue-86 pattern in `dna/brief.py`, but the new
streaming branch forgets to forward the `tools=[web_search]` argument
(and the shared `stream_and_emit` helper has no `tools` kwarg to
forward it through), so any caller using `task_id=...` gets an
un-grounded improvement brief — a silent regression of the module's
core promise. Fix by plumbing `tools` through `stream_and_emit` and
passing it from this call site; ship a regression test asserting the
streaming path receives the web_search tool config.
