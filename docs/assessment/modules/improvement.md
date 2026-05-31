# improvement — assessed 2026-05-31

Wave-2 re-assessment. Wave 1 did not touch `improvement/`. The slice is
unchanged on disk since 2026-05-30 12:32 (`improvement/brief.py` mtime), and
the surrounding call path (`routers/improvement.py`, `worker/tasks.py:1050-1149`)
is also unchanged in the parts the prior assessment relied on. Recent commits
(b464a34, e9a2c3f, ac4cf65, 3c8d83d, 74431e7) touch catalog-sync / dna / onboarding
/ ci only, not this module.

Slice covered: `improvement/__init__.py` (empty package marker, 0 lines),
`improvement/brief.py`. The invoking router and Celery task are owned by other
agents; read only to confirm the call path moved off the event loop and the
prompt-cache split, web_search tool wiring, and token logging are still correct.

## Findings

- [cleanup] improvement/brief.py:57,64 — typing gaps the mypy gate has not caught:
  the public parameter `analytics: dict` is un-parameterised, and the local
  `payload` at :64 is implicitly typed. Function return type `-> str` is correct;
  `dna_brief: str | None = None` is correct.
  | fix: annotate `analytics: dict[str, object]` (or a TypedDict matching the
  worker's `analytics = {...}` shape at worker/tasks.py:1119-1125 — the call site
  already passes that exact shape) and `payload: dict[str, object] = {...}` at
  :64.

- [cleanup] improvement/brief.py — Issue 86's progress-streaming primitive
  (`worker/progress.py` + `worker/anthropic_stream.py`, already used by
  `dna/brief.py`'s `generate_brief_streaming`) is NOT wired here. The user
  currently sees a static "pending" status for ~120s with no incremental
  feedback, while DNA builds stream per-token deltas to the onboarding UI. Not a
  defect — the 202/poll contract is correct on its own — but a clear consistency
  / UX follow-up now that the primitive exists.
  | fix (follow-up issue, not a Phase-4 blocker): extract a
  `generate_improvement_brief_streaming(...)` mirroring `dna/brief.py`'s pattern;
  have `_generate_improvement_brief_async` call it under a `task_id` channel; add
  a `stream_url` to the 202 response in `routers/improvement.py` so the UI can
  subscribe. Track as a new issue, not inline.

## Trace results (items the orchestrator asked to re-verify)

- **Prior SEV2 — 120s blocking request on the API path (Issue 75/78d):** still
  CLOSED. `routers/improvement.py:33` is `status_code=status.HTTP_202_ACCEPTED`
  and returns `{"status": "pending", "task_id": ...}` (line 94). The
  `_ANTHROPIC.with_options(timeout=120.0).messages.create(...)` at brief.py:71
  runs in the Celery worker via `asyncio.to_thread(build_brief, ...)` at
  worker/tasks.py:1130-1135, NOT on the API event loop. Idempotent on
  `(job_id, status==ready)` at worker/tasks.py:1091; debounced on in-flight
  pending at routers/improvement.py:73-74.
- **Per-creator isolation:** the worker's metrics query has
  `where(Video.creator_id == creator.id)` at worker/tasks.py:1107. The brief
  itself receives only pre-scoped aggregates + the creator's own DNA summary;
  no cross-tenant surface in this module.
- **Prompt caching (Issue 69):** still correct. `_SYSTEM_INSTRUCTIONS` is a
  static block carrying `cache_control: {"type": "ephemeral"}` (brief.py:74-81),
  followed by a SEPARATE per-creator analytics block (brief.py:82-83). The
  documented 2048-token-floor no-op behavior (brief.py:5-7, DECISIONS.md) is
  intentional, not a defect.
- **Token logging (rubric §5):** brief.py:98-104 logs `input_tokens`,
  `cache_read_input_tokens`, `cache_creation_input_tokens`, `output_tokens`. No
  PII / no prompt text in the log line.
- **web_search tool (rubric §5):** still wired via
  `settings.ANTHROPIC_WEB_SEARCH_TOOL` (brief.py:85), tool id resolved from
  `.env.example` (`web_search_20250305`). Final-text-block extraction
  (brief.py:106-112) handles the interleaved text/tool_use stream and raises
  `RuntimeError` on an empty response.
- **`max_tokens` / model id (rubric §5):** `max_tokens=2000` (brief.py:73);
  model from `settings.ANTHROPIC_MODEL` (`claude-sonnet-4-6`). Both present in
  `.env.example` with descriptions.
- **Client lifecycle (rubric §1):** module-level singleton
  `_ANTHROPIC = Anthropic(...)` (brief.py:24-28) with
  `timeout=httpx.Timeout(60.0, connect=10.0)` and `max_retries=2`; per-call
  `with_options(timeout=120.0)` for the long web_search path.
- **Honesty constraint (rubric §3):** prompt instructs "never promise virality"
  and "likelihood estimates, not guarantees" (brief.py:50,52); the
  Python-appended `_DISCLAIMER` (brief.py:30-34, always appended at :112) makes
  the honesty string structural, not LLM-dependent. No virality string anywhere.

## Security & compliance notes

- No OAuth tokens, no PII, no channel-identity secrets handled in this module.
  Only an aggregate analytics dict + a capped (`[:1000]`, brief.py:66) DNA
  summary reach the prompt. No `decrypt()` surface here.
- The single `logger.info` (brief.py:98) emits token counts only — no leak.
- The worker's `except` path stores a SAFE error string ("Brief generation
  failed — try again.") in the DB row (worker/tasks.py:1138) and logs the
  exception text via `logger.error` server-side only; the GET handler returns
  that safe string to the client. No stack trace / token / DB error surfaced.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — module-level singleton Anthropic client with timeout+retries; no DB/file handles in this module |
| 2 Concurrency & scale | ok — 120s LLM call runs in Celery via `asyncio.to_thread`, not on the API loop; idempotent on `(job_id, status==ready)`; debounced on in-flight |
| 3 Security & compliance | ok — no tokens/PII in logs or prompt; capped DNA payload; isolation enforced upstream; no virality promise; safe client-facing error |
| 4 Clip-quality | n/a (not a clip module) |
| 5 Anthropic SDK | ok — cache split correct (no-op below 2048 floor, documented); tokens logged with cache_read/cache_creation; web_search wired via config; final-block extraction correct; `max_tokens=2000`; model/tool from config |
| 6 Cleanliness & typing | 2 cleanup — `analytics: dict` under-parameterised at brief.py:57; Issue 86 streaming primitive not yet reused |
| 7 Error handling / API | n/a (router owns API surface; module raises `RuntimeError` for empty response, mapped to a SAFE message by the worker) |
| 8 Config & paths | ok — model/tool/key via `config.settings`; all three in `.env.example` with descriptions; no paths in module |

## Module verdict
clean — re-verified 2026-05-31. The improvement module is byte-identical to the
prior wave (mtime 2026-05-30 12:32) and the surrounding call path the rubric
depends on (router 202+poll, worker `asyncio.to_thread`, per-creator metrics
WHERE) is also unchanged. Prompt-cache split, web_search tool wiring, token
logging, model/tool config, isolation, idempotency, and the honesty constraint
all still hold. Two non-blocking cleanups remain: under-parameterised typing on
`analytics`/`payload`, and a UX follow-up to reuse Issue 86's progress-streaming
primitive so the brief's pending state isn't a 120s black box.
