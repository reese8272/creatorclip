# improvement — assessed 2026-05-29

Re-assessment after the hardening session (Issues 58–75). Scored against CURRENT code.

Slice: `improvement/__init__.py` (empty package marker), `improvement/brief.py`.
The invoking router (`routers/improvement.py`) is owned by another agent; it was
read only to trace `brief.py`'s call path (sync-on-loop question, Issue 66).

## Findings

- [SEV2] improvement/brief.py:71 / known-open Issue 75 — the brief is now offloaded
  off the event loop (router :68 wraps it in `await asyncio.to_thread(...)`, Issue
  66), so the prior loop-blocking SEV-1 is genuinely fixed. Residual defect: a single
  call can run the full 120s (`with_options(timeout=120.0)`), which can exceed a
  production LB/gateway timeout, and `asyncio.to_thread` uses the default bounded
  executor — concurrent briefs each hold one pool thread for up to 120s, so bursts
  can starve other `to_thread` work on the same worker. The 10/hour per-creator
  limiter caps per-creator volume but not cross-creator concurrency.
  | fix (already tracked, Issue 75): move to a Celery 202/poll job with result
  storage + poll endpoint, as `build_dna` does. Acceptable to defer; flagged so it
  is not lost. (needs-runtime-confirmation for the pool-starvation threshold.)

- [cleanup] improvement/brief.py:55-58,64 — typing gaps the mypy gate may not catch:
  `analytics: dict` should be `dict[str, object]` (or a TypedDict matching the
  router's payload), and local `payload` at :64 is untyped. Return type `-> str` is
  present and correct. | fix: annotate `analytics: dict[str, object]` and
  `payload: dict[str, object] = {...}`.

## Trace results (the items the orchestrator asked to verify)

- **Prompt-cache split**: FIXED / CORRECT. `system` is now two blocks — a static
  `_SYSTEM_INSTRUCTIONS` prefix carrying `cache_control: {"type": "ephemeral"}`
  (brief.py:74-80), then a SEPARATE uncached per-creator analytics block
  (brief.py:82). Volatile data is out of the cached block; the prior
  "interpolated-into-the-cached-prefix → ~0% hit" SEV1 is resolved (DECISIONS.md:198-223).
- **2048-token minimum (Sonnet 4.6)**: the static prefix (`_SYSTEM_INSTRUCTIONS`,
  ~16 lines, well under 2048 tokens) is BELOW Sonnet 4.6's 2048-token cacheable
  floor, so the `cache_control` breakpoint is structurally correct but a runtime
  NO-OP for this call. Correctly disclosed in the module docstring (brief.py:5-7)
  and DECISIONS.md:215-223. Not a defect — documented and intentional; the real
  caching beneficiary is `clip_engine/scoring.py` (out of slice). Model id confirmed
  `claude-sonnet-4-6` via `settings.ANTHROPIC_MODEL` (config.py:35).
- **Token logging after each call**: PRESENT. brief.py:97-103 logs `input_tokens`,
  `cache_read_input_tokens`, `cache_creation_input_tokens`, `output_tokens` via
  `getattr(..., 0)` defaults. No PII/token-secret in the line.
- **web_search final-block extraction (Issue 69)**: FIXED / CORRECT. brief.py:105-111
  filters `b.type == "text"` then returns `text_blocks[-1].text` (the synthesised
  answer after the last `tool_use`), not `[0]` (the "let me search…" preamble);
  raises `RuntimeError` if no text block exists (:106-107). `max_tokens=2000` (:72),
  `tools=[{type: settings.ANTHROPIC_WEB_SEARCH_TOOL, name: web_search}]` wired (:84).
- **120s brief — sync on loop or moved?**: still SYNCHRONOUS work, but no longer ON
  the loop. brief.py:71 is a blocking `_ANTHROPIC.with_options(timeout=120.0)
  .messages.create(...)`; router :68 offloads it via `asyncio.to_thread`. It has NOT
  yet moved to a 202/poll Celery job — that is the known-open follow-up (Issue 75,
  DECISIONS.md:312-317). See SEV2 above.
- **Other blocking calls**: none beyond the single `messages.create`. `json.dumps`
  (:68) is trivial. Client is a module-level singleton (`_ANTHROPIC`, brief.py:24-28)
  with `timeout=httpx.Timeout(60.0, connect=10.0)` + `max_retries=2` — no per-call
  construction (Issue 58/63 singleton fix verified).
- **Model/tool-version hardcoding** (prior SEV2): RESOLVED. `claude-sonnet-4-6` and
  `web_search_20250305` now come from `settings.ANTHROPIC_MODEL` /
  `settings.ANTHROPIC_WEB_SEARCH_TOOL` (config.py:35-36), present in `.env.example`
  with descriptions (.env.example:11-12). No duplicated literal in this module.
- **No virality promise**: CONFIRMED. The prompt explicitly instructs "never promise
  virality" / "likelihood estimates, not guarantees" (brief.py:50,52), and the
  Python-appended `_DISCLAIMER` (brief.py:30-34, always appended at :111, never left
  to the LLM) states "does not promise virality or specific growth outcomes."
  Structurally compliant with the Honesty Constraint.

## Security & compliance notes

- No OAuth tokens, PII, email, or channel-identity secrets handled in this module;
  only an aggregate analytics dict + a capped (`[:1000]`, brief.py:66) DNA summary
  reach the prompt — consistent with COMPLIANCE.md "no analytics beyond what the
  analysis needs". No `decrypt()` surface here.
- Per-creator isolation: this module receives pre-scoped data; the creator-scoped
  `WHERE Video.creator_id == creator.id` lives in the router (out of slice) and was
  verified present (routers/improvement.py:33) — the Issue 33 SEV-0 leak is closed.
- The single `logger.info` (brief.py:97) emits token counts only — no leak.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — module-level singleton client (:24-28) with timeout+retries; no DB/file handles |
| 2 Concurrency & scale | 1 SEV2 — blocking call now off-loop (Issue 66) but 120s duration + bounded threadpool unresolved (Issue 75) |
| 3 Security & compliance | ok — no tokens/PII; capped DNA payload; isolation enforced upstream; no virality promise |
| 4 Clip-quality | n/a (not a clip module) |
| 5 Anthropic SDK | ok — cache split correct (no-op below 2048 floor, documented); tokens logged; web_search wired; final-block extraction correct; max_tokens set; model/tool from config |
| 6 Cleanliness & typing | 1 cleanup — `analytics: dict` / local `payload` under-parameterised; no TODO/print/dead code |
| 7 Error handling / API | n/a (router owns API surface; module raises `RuntimeError` for empty response, mapped to 502 upstream) |
| 8 Config & paths | ok — model/tool/key via `config.settings`, all in `.env.example` with descriptions; no paths in module |

## Module verdict
NEEDS-WORK — every previously-flagged SEV1 (dead cache split, sync-on-loop call,
wrong web_search extraction) and the model-hardcoding SEV2 are now resolved and
verified. The only substantive item left is the documented, already-tracked SEV2:
the 120s synchronous brief is off the event loop but still a long blocking request
that should become a Celery 202/poll job (Issue 75).
