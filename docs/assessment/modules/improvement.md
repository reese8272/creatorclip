# improvement — assessed 2026-06-07

## Findings

- [SEV2] brief.py:38-53 — Static `_SYSTEM_INSTRUCTIONS` prefix is intentionally
  too short (~150 tokens) to engage Sonnet 4.6's 1024-token cache floor, so the
  `cache_control` breakpoint on line 88 is inert; every call pays full input-token
  cost on the system block. The docstring (lines 5-7) and `docs/DECISIONS.md`
  acknowledge the tradeoff, but caching is mandated by CLAUDE.md for every
  Anthropic call. | fix: either (a) inline the per-call analytics-shape contract
  + a short principles excerpt from `docs/CLIPPING_PRINCIPLES.md` into the static
  block to push the stable prefix above 1024 tokens, then verify
  `cache_creation` > 0 once and `cache_read` > 0 thereafter; or (b) update
  `docs/DECISIONS.md` to formally exempt this low-frequency endpoint and add a
  pointer from this docstring. Today's wording reads as "we tried and gave up"
  rather than a decision.
- [SEV2] brief.py:67 — `_build_request` returns `tuple` (untyped element types);
  mypy cannot catch a caller unpacking the wrong arity, and the streaming /
  non-streaming paths both unpack three values without static guarantee. | fix:
  annotate `-> tuple[list[dict], list[dict], list[dict]]` and update the unpack
  sites at lines 125 to match.
- [SEV2] brief.py:179 — `RuntimeError("Claude returned no text...")` is raised
  bare in the `.create()` path; worker/tasks.py catches it broadly and marks the
  brief `failed`, but the message itself is what surfaces to logs (line 2077),
  not to clients, so this is bounded. Still: the streaming path at lines 139-147
  has no equivalent guard — if `stream_and_emit` returns an empty `final_text`
  (e.g. the model emitted only `tool_use` blocks before stopping), the
  disclaimer is appended to an empty string and persisted as a "ready" brief. |
  fix: add `if not final_text: raise RuntimeError("Claude returned no text in
  streaming improvement brief")` immediately after the `stream_and_emit` call
  (after line 147) so both paths fail loud and Celery retries.
- [SEV2] brief.py:25-29 — `_ANTHROPIC` is constructed with a 60s timeout, then
  every caller overrides it to 120s via `.with_options(timeout=120.0)` (lines
  138, 161). The module-level timeout is dead config that misleads readers
  about the live budget. | fix: drop `timeout=httpx.Timeout(60.0, connect=10.0)`
  from the constructor and keep `max_retries=2`; document at the top of the
  module that callers set per-call timeouts because web_search latency varies
  60-120s.
- [cleanup] brief.py:80, 93, 94 — `system: list[dict]`, `tools: list[dict]`,
  `messages = [...]` are typed as bare `list[dict]` rather than `list[dict[str,
  object]]` (or the SDK's TypedDicts). Mypy already passes (the gate is fine),
  but readers hit `dict` with no value type. | fix: tighten to `list[dict[str,
  object]]` to match the `payload` annotation on line 74.
- [cleanup] brief.py:134 — `from worker.anthropic_stream import stream_and_emit`
  is a function-local import to break a circular dependency between
  improvement/ and worker/. Acceptable, but the *reason* is not documented; a
  future refactor will move it to module-top and break the cycle. | fix: add a
  one-line comment "local import: worker.anthropic_stream imports config which
  imports settings — keep this function-local to avoid a worker → improvement
  → worker cycle" (verify the actual cycle shape before committing the wording).

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — `_ANTHROPIC` is a module-level singleton (line 25); `.with_options` returns a wrapped client without re-opening the HTTP pool; no DB session in this module (worker/tasks.py owns the session for the streaming call site); no file handles or subprocesses |
| 2 Concurrency & scale | ok — module is fully synchronous; the Celery task at worker/tasks.py:2065 wraps `build_brief` in `asyncio.to_thread`, so the 60-120s blocking SDK call never sits on the event loop; bounded work (max_tokens=2000, single LLM call per request, DNA brief sliced to 1000 chars at line 76); no N+1 |
| 3 Security & compliance | ok — no DB queries in this module (per-creator isolation enforced at the call site in worker/tasks.py:1981, 2034); `ANTHROPIC_API_KEY` read via `settings` only (line 26), never logged; token-count logs (lines 148-154, 169-175) include no PII or prompt text; honesty disclaimer appended in Python at line 158 + 183 (never left to the LLM); no virality promise anywhere in the system block (lines 38-53 explicitly say "never promise virality") |
| 4 Clip-quality | n/a (improvement brief is content-strategy guidance, not clip scoring) |
| 5 Anthropic SDK | 1 SEV2 — cache breakpoint is structurally correct (stable prefix → `cache_control: ephemeral` → volatile per-creator block) but the prefix is below the 1024-token engagement floor, so caching is a no-op; token logging present on both paths; `max_tokens=2000` set; structured `web_search` tool wired through both `.create()` and streaming via the Wave-3 Fix A `tools` kwarg |
| 6 Cleanliness & typing | 2 SEV2 + 2 cleanup — `_build_request -> tuple` is unparameterised; missing empty-text guard on the streaming path; dead 60s timeout on the singleton; `list[dict]` annotations could tighten |
| 7 Error handling / API | n/a (no router/endpoint in this module; the FastAPI surface for improvement briefs lives elsewhere) |
| 8 Config & paths | ok — `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `ANTHROPIC_WEB_SEARCH_TOOL` all present in config.py (lines 13, 51, 57); no filesystem paths in the module; `_DNA_BRIEF_MAX_CHARS = 1000` is a documented constant (lines 56-60) |

## Module verdict

NEEDS-WORK — no blockers, but four SEV2 items worth fixing before public launch:
inert prompt-cache breakpoint (mandated by CLAUDE.md), an unguarded empty-text
case on the streaming path that can persist a blank brief as "ready", a
loosely-typed `tuple` return that hides arity bugs from mypy, and a misleading
module-level timeout that's overridden on every call. Security, token logging,
per-creator isolation (enforced upstream), and the web_search wiring are sound.
