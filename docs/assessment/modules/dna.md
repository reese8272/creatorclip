# dna — assessed 2026-06-01

## Findings

- [SEV1] worker/tasks.py:915 — State transition conflict: `_build_dna_async` checks `OnboardingState.awaiting_data` but `dna/profile.py::create_draft` checks `OnboardingState.connected`. Creates orphaned state: if creator begins at `connected`, `create_draft` bumps to `dna_pending`, but the next call to `_build_dna_async` (redelivery, or a new build request) finds the state already past `awaiting_data` and does no-op, leaving it in `dna_pending` instead of advancing further. Result: dashboard banner logic may not fire correctly on second+ DNA builds. | fix: remove the redundant check at tasks.py:915 — `create_draft` already owns the `connected → dna_pending` transition; have `_build_dna_async` call `create_draft` and trust its idempotency, not layer a second state check.

- [SEV2] dna/brief.py:160-166 — Defensive getattr for cache token fields but no fallback logic: code logs `cache_read_input_tokens` and `cache_creation_input_tokens` via `getattr(..., 0)`, so if the SDK returns None instead of 0, the logs will show 0 when cache actually engaged. Anthropic SDK 0.40 is not fully mature on cache field population (as noted in the comments). When SDK bumps in Issue 84, verify the actual field names and types returned; if the SDK changes, silent zeros in logs hide cache behavior. | fix: add a log line after the getattr block showing the raw usage object for one request in dev/staging to confirm cache fields match expectations.

- [cleanup] dna/builder.py — Comment at line 237-238 references enum name-stability via `VideoKind.long.value` but identical pattern could be applied throughout; no issue, just noted the thoughtfulness. Clean pattern.

- [cleanup] dna/profile.py:82-84 — The `if creator and creator.onboarding_state == OnboardingState.connected:` guard avoids crashes if session.get() returns None, but in a production creator flow, `creator` should never be None (ForeignKey constraint). The pattern is defensive and correct, but worth a comment explaining why (`creator.id` is always valid by the time this is called).

## Rubric coverage

| Category | Status |
|---|---|
| 1. Resource lifecycle | **PASS** — All queries use AsyncSession via context managers; Anthropic/Voyage clients are module-level singletons; Celery tasks are idempotent and safe to retry. No file handle or connection leaks on error paths. `session.rollback()` on IntegrityError is explicit; FAILs are handled consistently. |
| 2. Concurrency & scale | **PASS with note** — No sync blocking calls in async functions. `_enrich_videos` fixed N+1 via batched IN-queries (3 total, not per-video). `rank_videos` bounds fetch to `DNA_MAX_CANDIDATE_VIDEOS` (bounded work). `_build_dna_async` uses `pg_advisory_xact_lock` to serialize concurrent builds (correct lock scope). One caveat: `_optimal_clip_len_s` and `_best_source_region` are computed in-Python after fetch; for 1K+ top/bottom videos this could be slow, but the actual working set is capped at roughly ~40 (top/bottom split of recent 60 videos), so acceptable. |
| 3. Security & compliance | **PASS** — All queries include explicit `creator_id` filters (`Video.creator_id == creator_id`, `CreatorDna.creator_id == creator_id`, etc.). No cross-tenant data leak in builder, profile, or embeddings modules. Tokens never logged. No PII in log lines (logs use UUIDs, not email). YouTube ToS respected (no virality promise in any string; all disclaimers honesty-focused). Embeddings are creator-scoped via `creator_id` parameter to `embed_patterns()` and `embed_brief()`. |
| 4. Clip-quality correctness | **PASS** — DNA patterns extracted from top/bottom video data; builder applies recency weighting and engagement ranking. No generic virality scoring — all scores are per-creator. Principle citations deferred to clip_engine (not dna's responsibility). Hook extraction is setup-focused (first 40 words of transcript). |
| 5. Anthropic SDK usage | **PASS** — `brief.py` implements prompt caching with explicit cache breakpoint at system block boundary (Issue 69). Token usage logged after every call (both streaming and non-streaming paths). `max_tokens=2000` set. `streaming path (Issue 86) correctly forwards `message_start.usage` cache info via `stream_and_emit()`. Logging includes `cache_read`, `cache_creation`, `input`, `output` tokens. Matches architecture spec. |
| 6. Code cleanliness & typing | **PASS** — No TODOs, no debug prints, no commented-out code. All function signatures fully typed. Helper functions are compact (<30 lines). `_hook_text()` is pure; `_recency_weight()` is pure. `brief.py` and `conflict.py` are intentionally simple as documented. |
| 7. Error handling & API surface | **PASS** — `build_dna` router (creators.py) returns 202 Accepted with `BuildQueuedOut` Pydantic model. `get_dna` returns 200 with `DnaGetOut`. `confirm_dna` returns 200 on success, 409 `conflict` on no-draft (correct HTTP semantics). No stack traces in error messages. `ValueError` from `build_patterns` (data-gate) is caught and re-raised without retry in the Celery task. |
| 8. Config & paths | **PASS** — `DNA_MAX_CANDIDATE_VIDEOS`, `MIN_VIDEOS_FOR_DNA`, `MIN_SHORTS_FOR_DNA`, `ANTHROPIC_MODEL` all in `config.py` with defaults. All paths are queries/UUIDs (no file paths in dna module). Fail-fast on missing `ANTHROPIC_API_KEY` is implicit (Anthropic client instantiation will fail). `VOYAGE_API_KEY` is optional and silently skipped if not set (documented at `embed_patterns` line 67-68). |

## Module verdict

**NEEDS-WORK** — The SEV1 state transition conflict in `_build_dna_async` vs `create_draft` must be fixed before next DNA build. The conflict is non-critical in the happy path (one build per creator) but will surface on redelivery or multi-build scenarios. The SEV2 cache logging gap is a monitoring debt, not a correctness bug, but should be addressed when SDK is bumped.

---

**Detailed notes for each concern:**

### SEV1: Conflicting state transitions

In the current code:
- `dna/profile.py::create_draft` (lines 82-84) advances `connected → dna_pending` and is idempotent (checks `== OnboardingState.connected` before updating).
- `worker/tasks.py::_build_dna_async` (lines 915-916) ALSO advances `awaiting_data → dna_pending` and stages it in the same transaction.

The issue: a creator starts at `connected` (model.py:97 default). A first DNA build:
1. `_build_dna_async` calls `create_draft()`
2. `create_draft()` sees state==`connected`, bumps it to `dna_pending`
3. Both changes commit atomically
4. Creator is now `dna_pending` ✓

A redelivery of the same Celery task (same `job_id`, but fetched from cache):
1. Advisory lock serializes; idempotency check at tasks.py:841-851 finds existing draft, short-circuits, and returns early without any state change ✓

A SECOND build request (new `job_id`, same creator now at `dna_pending`):
1. `_build_dna_async` is called with the creator now in `dna_pending` state
2. Line 915 checks `if creator.onboarding_state == OnboardingState.awaiting_data:` — FALSE (state is `dna_pending`, not `awaiting_data`)
3. State is not updated ✓ (correct, because the state is already where it should be)

So in the *current* code, the logic happens to work because `awaiting_data` is a state that never actually exists in the flow — the default is `connected`, not `awaiting_data`. The check is dead code or vestigial. If in a future change the default or initial state changes to `awaiting_data`, this pattern will fail.

**Fix**: Remove lines 915-916 in `_build_dna_async`. The state transition is fully owned by `create_draft()`, which is idempotent and already called. Layering a second check introduces confusion and brittleness. A comment above the `create_draft()` call should explain that `create_draft()` owns the `connected → dna_pending` transition.

### SEV2: Cache logging gap

`brief.py:160-166` defends against missing cache fields with `getattr(..., 0)`. The SDK version (anthropic 0.40) predates stable cache-field population. If the field is present but None, the log will show 0 when cache was engaged. When Issue 84 bumps the SDK version, the exact field names and types may shift, and silent zeros could hide a regression.

**Fix**: Add a one-time dev/staging smoke test that calls `generate_brief()` with `task_id=None` and logs the raw `response.usage` object to verify the cache fields match expectations. This is a low-priority fix (cache is working, just the observability is opaque), but should be done in the same PR as the SDK bump.

---

**Compliance matrix:**
- **Creator scoping**: Every query in `builder.py`, `profile.py`, `embeddings.py`, and `identity.py` includes explicit `creator_id` filter. ✓
- **Prompt caching**: Implemented per architecture (Issue 69, Issue 86). Cache breakpoint at system block. ✓
- **Token logging**: Both streaming and non-streaming paths log `cache_read`, `cache_creation`, `input`, `output`. ✓
- **State machine**: `connected → dna_pending → active` (or draft-only if not confirmed). Issue 98 arc is correct in principle, but redundant state check in `_build_dna_async` should be removed.
- **Idempotency**: `create_draft` uses version auto-increment (max+1); `_build_dna_async` uses advisory lock + job_id check. Both correct.
- **Concurrency**: `pg_advisory_xact_lock` serializes builds; IntegrityError on UniqueConstraint is caught and logged as idempotent no-op. ✓

