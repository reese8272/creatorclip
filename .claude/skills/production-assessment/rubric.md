# Per-Module Assessment Rubric

This is the fixed lens every Layer-1 subagent scores its module against. It is
drawn directly from the CLAUDE.md Phase-4 REVIEW checklist so that the assessment
and the per-issue workflow measure the same things. Score every applicable item;
mark `n/a` with one word of reason when a category does not apply to the module.

Severity scale:
- **BLOCKER** — ships a bug, leak, or outage at scale; must fix before launch.
- **SEV1** — correctness/security defect that will bite under load or over time.
- **SEV2** — real defect, bounded blast radius, fix soon.
- **cleanup** — DRY/KISS/typing/naming; no behavior risk.

---

## 1. Resource lifecycle
- DB sessions acquired via context manager, guaranteed close on every path
  (including exceptions / early return).
- External clients (Anthropic, Voyage, YouTube, R2/storage) are module-level
  singletons, not per-call constructions.
- Celery tasks idempotent under at-least-once delivery and safe to run twice
  concurrently; temp media cleaned up in a `finally`.
- No connection / file handle / subprocess leak on the error path.

## 2. Concurrency & scale (load-bearing for hundreds of users — see scale-checklist.md)
- No sync/blocking call hidden inside an `async def` (requests, time.sleep,
  subprocess.run, blocking DB driver, heavy CPU on the loop thread).
- Shared async resources (engine/pool, redis client) bound to the right loop;
  not recreated per request/task.
- Queries that run per-request are indexed for the access pattern; no N+1.
- Bounded work: no unbounded `fetchall`, no unbounded fan-out, no unbounded
  in-memory accumulation of per-creator data.

## 3. Security & compliance (load-bearing)
- OAuth tokens read via `decrypt()`; never logged, never returned in a response.
- No PII or secret in any log line (grep the module's `logger.*` calls).
- **Per-creator isolation on EVERY query** touching a creator-scoped table —
  a missing `WHERE creator_id = ?` is a cross-tenant leak. Treat as BLOCKER.
- Parameterized SQL only; no f-string/`%`-built queries.
- YouTube ToS / retention respected; source-media purge honored.
- No virality promise in any string, response, or prompt.

## 4. Clip-quality correctness (clip_engine / dna / preference only)
- Clip start anchored to the setup (backward look from peak), not the aftermath.
- Every score cites a named principle from `docs/CLIPPING_PRINCIPLES.md`.
- Ranking is against THIS creator's DNA + audience, not a generic score.
- Preference model applies exponential recency decay; below-threshold fallback
  to DNA + signals is honest and explicit.

## 5. Anthropic SDK usage (any module calling the LLM)
- Prompt caching used (mandatory per architecture).
- Token usage logged after every call.
- Structured output / token limits set; web-search tool used where live research
  is intended.

## 6. Code cleanliness & typing
- No TODO, no commented-out code blocks, no `print()`/debug statements.
- No duplicated logic (DRY) — flag the second occurrence with a pointer to the first.
- Every function signature typed (CLAUDE.md mandates this — the mypy gate enforces
  it mechanically, but flag obvious gaps the gate hasn't caught yet).
- Functions over ~30 lines that do more than one thing (KISS / single responsibility).

## 7. Error handling & API surface (routers only)
- Pydantic model on every request and response.
- Correct HTTP status codes (200/400/401/404/422/500).
- Error messages safe — no stack trace, no DB error, no internal detail to client.

## 8. Config & paths
- All paths absolute.
- Any new config present in `.env.example` with a description.
- Fail-fast on missing required config (pydantic-settings).

---

## What NOT to flag
- Style the formatter/linter already owns (line length, quotes, import order) —
  ruff handles it; do not duplicate.
- Speculative abstractions for scale that isn't in the PRD ("you might one day
  need…") — KISS. Flag only concrete, present defects.
