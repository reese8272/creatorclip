# improvement — assessed 2026-05-30

Re-assessment after Issue 78d (Celery 202+poll) and Issue 86 (progress streaming).
Slice: `improvement/__init__.py` (empty package marker), `improvement/brief.py`.
The invoking router (`routers/improvement.py`) and the Celery task
(`worker/tasks.py:937-1036`) are owned by other agents; read only to confirm the
call path moved off the event loop.

## Findings

- [cleanup] improvement/brief.py:57,64 — typing gaps the mypy gate has not caught:
  the public parameter `analytics: dict` is un-parameterised, and the local
  `payload` at :64 is implicitly typed. Function return type `-> str` is correct;
  `dna_brief: str | None = None` is correct.
  | fix: annotate `analytics: dict[str, object]` (or a TypedDict matching the
  worker's `analytics = {...}` shape at worker/tasks.py:1006-1012 — the call site
  already passes that exact shape) and `payload: dict[str, object] = {...}` at
  :64.

- [cleanup] improvement/brief.py — Issue 86's progress-streaming primitive
  (`worker/progress.py` + `worker/anthropic_stream.py`, already used by
  `dna/brief.py`'s `generate_brief_streaming` per worker/tasks.py:577,638) is NOT
  wired here. The user currently sees a static "pending" status for ~120s with no
  incremental feedback, while DNA builds stream per-token deltas to the
  onboarding UI. Not a defect — the 202/poll contract is correct on its own — but
  a clear consistency / UX follow-up now that the primitive exists.
  | fix (follow-up issue, not a Phase-4 blocker): extract a
  `generate_improvement_brief_streaming(...)` mirroring `dna/brief.py`'s pattern;
  have `_generate_improvement_brief_async` call it under a `task_id` channel; add
  a `stream_url` to the 202 response in `routers/improvement.py` so the
  onboarding UI can subscribe. Track as a new issue, not inline.

## Trace results (items the orchestrator asked to verify)

- **Prior SEV2 — 120s blocking request on the API path (Issue 75/78d):** CLOSED.
  `routers/improvement.py:31-94` is now `status_code=202_ACCEPTED`, returns
  `{"status": "pending", "task_id": ...}`, and dispatches via
  `generate_improvement_brief_task.delay(str(creator.id))`. The actual
  `_ANTHROPIC.with_options(timeout=120.0).messages.create(...)` (brief.py:71)
  runs in the Celery worker (`worker/tasks.py:1017` —
  `asyncio.to_thread(build_brief, ...)`), not on the API event loop. The
  load-balancer-timeout failure mode is gone. The poll handler is a single
  indexed read (`SELECT … WHERE creator_id = ?`, line 105-107).
- **Idempotency under at-least-once delivery:** present. The worker checks
  `row.job_id == job_id and row.status == ready` (worker/tasks.py:978-980) and
  short-circuits before the paid LLM call on redelivery. The POST also debounces
  in-flight builds without re-enqueuing (routers/improvement.py:73-74).
- **Issue 86 progress streaming reuse opportunity:** confirmed UN-wired here.
  `worker/progress.py` and `worker/anthropic_stream.py` exist and are used by
  `dna/brief.py` (via `generate_brief_streaming`, called from
  `worker/tasks.py:577` with `_emit("done", ...)` at :638), but
  `improvement/brief.py` still uses the non-streaming `messages.create(...)`. Not
  a defect today; flagged above as a follow-up.

## Items previously verified that remain CORRECT (carry-forward, no re-flag)

- **Prompt-cache split (Issue 69):** `system=` is a static `_SYSTEM_INSTRUCTIONS`
  block with `cache_control: {"type": "ephemeral"}` (brief.py:74-81), followed by
  a SEPARATE per-creator analytics block (brief.py:82-83). The volatile data is
  out of the cached prefix; the prior "interpolated into the cached prefix → ~0%
  hit" SEV1 stays resolved (DECISIONS.md:198-223).
- **2048-token cacheable floor:** the static prefix is well under Sonnet 4.6's
  2048-token cache floor, so the `cache_control` breakpoint is a runtime no-op
  for this low-frequency call — documented in the module docstring (brief.py:5-7)
  and DECISIONS.md:215-223. Intentional; not a defect.
- **Token logging:** brief.py:98-104 logs `input_tokens`, `cache_read_input_tokens`,
  `cache_creation_input_tokens`, `output_tokens`. No token/PII content in the
  line.
- **web_search final-block extraction (Issue 69):** brief.py:106-112 filters
  `b.type == "text"` then returns `text_blocks[-1].text + _DISCLAIMER`; raises
  `RuntimeError` if no text block exists. `max_tokens=2000` (:73);
  `tools=[{type: settings.ANTHROPIC_WEB_SEARCH_TOOL, name: "web_search"}]` wired
  via config (:85).
- **Model & tool-id from config:** `claude-sonnet-4-6` and
  `web_search_20250305` come from `settings.ANTHROPIC_MODEL` and
  `settings.ANTHROPIC_WEB_SEARCH_TOOL`, both in `.env.example` with
  descriptions; no module-local literals.
- **Client lifecycle:** module-level `_ANTHROPIC = Anthropic(...)` singleton
  (brief.py:24-28) with `timeout=httpx.Timeout(60.0, connect=10.0)` and
  `max_retries=2`; per-call `with_options(timeout=120.0)` for the web_search
  override.
- **No virality promise:** prompt instructs "never promise virality" and
  "likelihood estimates, not guarantees" (brief.py:50,52); the Python-appended
  `_DISCLAIMER` (brief.py:30-34, always appended at :112) makes the honesty
  string structural, not LLM-dependent.

## Security & compliance notes

- No OAuth tokens, no PII, no channel-identity secrets handled in this module.
  Only an aggregate analytics dict + a capped (`[:1000]`, brief.py:66) DNA
  summary reach the prompt — consistent with COMPLIANCE.md "no analytics beyond
  what the analysis needs". No `decrypt()` surface here.
- Per-creator isolation: this module receives pre-scoped data; the
  creator-scoped `WHERE Video.creator_id == creator.id` lives in the worker's
  metrics query (worker/tasks.py:994), the existence check
  (routers/improvement.py:59), and the row fetch
  (routers/improvement.py:71,106). Issue 33 SEV-0 leak remains closed.
- The single `logger.info` (brief.py:98) emits token counts only — no leak.
- The worker's `except` path stores a SAFE error string ("Brief generation
  failed — try again.") in the DB row and logs the exception text via
  `logger.error` (server-side only); the GET handler returns that safe string to
  the client. No stack trace or token surfaced.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — module-level singleton client (:24-28) with timeout+retries; no DB/file handles in this module |
| 2 Concurrency & scale | ok — prior SEV2 resolved by Issue 78d; the 120s LLM call now runs in Celery via `asyncio.to_thread`, not on the API loop; idempotent on `(job_id, status)` |
| 3 Security & compliance | ok — no tokens/PII; capped DNA payload; isolation enforced upstream; no virality promise; safe error messages |
| 4 Clip-quality | n/a (not a clip module) |
| 5 Anthropic SDK | ok — cache split correct (no-op below 2048 floor, documented); tokens logged; web_search wired; final-block extraction correct; max_tokens set; model/tool from config |
| 6 Cleanliness & typing | 2 cleanup — `analytics: dict` under-parameterised; Issue 86 streaming primitive not yet reused |
| 7 Error handling / API | n/a (router owns API surface; module raises `RuntimeError` for empty response, mapped to a SAFE message by the worker) |
| 8 Config & paths | ok — model/tool/key via `config.settings`, all in `.env.example` with descriptions; no paths in module |

## Module verdict
clean — the prior SEV2 (120s blocking request) is closed by Issue 78d; the
Anthropic SDK, cache split, web_search extraction, model/tool config, isolation,
idempotency, and honesty constraint are all correct and verified. Only two
non-blocking cleanups remain: under-parameterised typing on `analytics`/`payload`,
and a UX follow-up to reuse Issue 86's progress-streaming primitive so the
brief's pending state isn't a 120s black box.
