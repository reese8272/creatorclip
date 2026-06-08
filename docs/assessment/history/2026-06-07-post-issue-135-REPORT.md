# CreatorClip — Production Assessment

**Date:** 2026-06-07 (post-Issue-135)  ·  **Commit:** `7af18b2`  ·  **LOC:** ~52,000 (incl. static/tests)  ·  **Tests:** 889 passed / 2 skipped / 126 deselected

## VERDICT: PRODUCTION-READY — **CONDITIONAL**

No BLOCKERs. **6 SEV1s** across 3 modules (worker, knowledge, youtube) — all with concrete one-commit fixes. The most consequential is a **cross-cutting routers/`async def` axis-B violation**: ~15 sync `task.delay(...)` enqueues sit inside async handlers and will block the event loop on Redis I/O at concurrency. Conditional on (a) the 6 SEV1 fixes + (b) the routers `task.delay` cleanup + (c) the not-yet-run Locust load test to close scale-checklist axes A and E with evidence.

---

## Layer 0 — deterministic gates (from `_machine.json`)

| Gate | Result | Baseline | Status |
|---|---|---|---|
| ruff | 0 issues | 0 | ✅ |
| mypy | 0 errors | 0 | ✅ |
| coverage | 75.29 % | 75.20 % | ✅ |
| bandit | high 0 / med 0 | high 0 / med 0 | ✅ |
| pip-audit | 0 vulns | 0 | ✅ (`.venv` resync to `aiohttp==3.14.1` cleared a transient stale-venv false positive; `requirements.txt` was already correct) |
| freshness | 9 / 9 / 9 days | 90-day threshold | ✅ |

**CI lessons banked this session:**
- CI's `pip install ruff` is now pinned to `0.15.15` to match `.venv` (Issue 134 push hit a format-drift failure when CI's unpinned ruff bumped past local).
- Deploy workflow auto-runs `alembic upgrade head` before container rollout — no manual migration step on prod. The prior LEFT_OFF guidance about pending migrations was incorrect; verified live this session.

**Top uncovered load-bearing paths:**
1. `clip_engine/render.py::render_cleaned_clip_file` filter_complex script construction — covered by unit assertions but the end-to-end ffmpeg invocation is shell-mocked in tests; first prod render under Issue 134/135 is the real signal.
2. `worker/tasks.py::_clean_clip_async` + `_edit_clip_async` — covered by validation tests; the R2 upload + cleanup path is shell-mocked.
3. `static/editor.js` — the `getSelection()`-snapped word-boundary walker has no JS test (the project doesn't run a JS test runner); first real-browser session is the integration signal.

---

## Layer 1 — module register (ranked)

Tally across all 13 modules: **0 BLOCKER · 6 SEV1 · 57 SEV2 · 54 cleanup**

| Sev | Module | Location | Issue | Backed fix |
|---|---|---|---|---|
| SEV1 | worker | `tasks.py:874+1006` | `_clean_clip_async` and `_edit_clip_async` share `Clip.cleaned_render_uri` as the idempotency check — running `/clean` then `/cuts` (or vice versa) silently short-circuits the second task and the user's work is dropped. Same UX hole I flagged in Issue 135 LEFT_OFF as a known limit — the subagent escalated to SEV1 because the failure is silent (200 OK from the endpoint, "done" SSE event, no R2 upload). | Either (a) add `original_render_uri` column + separate `edited_render_uri` so the two flows have independent slots, OR (b) keep the shared column but make the worker error-on-conflict instead of silent-noop. Route /clean and /cuts to reject when the other artifact is pending. |
| SEV1 | worker | `tasks.py:346` (`_retrain_preference_async`) and `tasks.py:1978` (`_generate_improvement_brief_async`) | Use `db.AsyncSessionLocal()` (app-role engine, subject to Issue 79 RLS) but never set `session.info["creator_id"]`. Under the production role split, `current_setting('app.creator_id', true)` returns NULL → RLS `USING (creator_id = NULL)` matches no rows → retrain marks the model as fit with zero data, brief writes empty payload, creator sees a silent broken state. | Either switch to `AdminSessionLocal()` (worker-internal task, bypasses RLS), or stamp `session.info["creator_id"] = str(creator_id)` before the first query so the existing `after_begin` listener runs `SET LOCAL app.creator_id = …`. |
| SEV1 | knowledge | `hooks.py:176`, `chapters.py:182` | `cache_control: {"type": "ephemeral"}` on the system block, but the call runs against Haiku 4.5 whose cacheable-prefix minimum is **4096 tokens**. The static system block + DNA brief ≈ 900 tokens — well below the floor. Cache marker is inert; every call pays full input-token cost. Token log silently reports `cache_read=0`. Same trap as `improvement/brief.py` (already noted in DECISIONS). | Drop the `cache_control` entry on both files and add a `docs/DECISIONS.md` note that caching is intentionally off for these low-frequency calls (matches improvement/brief.py pattern). The mandatory-caching rule applies to high-frequency calls only — see DECISIONS for the existing precedent. |
| SEV1 | youtube | `oauth.py:243,258` | `_do_token_refresh` calls `session.commit()` inside what is usually a caller-owned `AsyncSession`. `get_valid_access_token` is invoked from request handlers and Celery tasks that already hold an open transaction — committing here flushes unrelated pending writes. | Accept an explicit `commit_caller_session: bool = False` flag, OR open an internal session scoped to the token-write only, OR document that callers must call this BEFORE starting their transaction. |
| SEV2-top | routers | 15 sites across `clips.py`, `videos.py`, `creators.py`, `auth.py`, `improvement.py`, `analysis.py`, `thumbnails.py`, `titles.py` | Sync `task.delay(...)` / `start_pipeline(...)` Celery enqueues inside `async def` handlers — blocks the event loop on Redis I/O on every enqueue. Under 100s of concurrent users this is the p99 cliff. Scale-checklist axis B. | Wrap each in `await asyncio.to_thread(task.delay, ...)`. Or hoist to a shared `_enqueue_task` helper. ~15-line cross-cutting fix. |
| SEV2 | worker | `tasks.py` various | `parse_concepts` failure path verification, bare `except Exception` on UUID parse — see `worker.md`. | See module file. |
| SEV2 | clip_engine | `render.py:219` | `subtitles={ass_path}:fontsdir=...` filter built with no libass escaping of `:`/`\`/`'` in `ass_path`. /tmp paths are safe today but a future non-/tmp out_path crashes the render at filter-parse time. | Escape via the libass `:` / `\\` / `\'` rules before string-formatting. |
| SEV2 | ingestion | `audio.py:46,49` | Per-clip-peak RMS normalisation makes the absolute energy/silence thresholds meaningless for uniformly quiet or noisy recordings. | Use a fixed dB reference (e.g., -28 dBFS) like auto-editor; calibrate against the audio file's peak. |
| SEV2 | dna | `builder.py:88` | `_optimal_upload_gap_h` doesn't wrap the week — biases the cadence recommendation surfaced in the brief. | Compute as `min(direct_gap, 168 - direct_gap)` for the circular distance. |
| SEV2 | billing | `stripe_client.py:101` | Stripe `Idempotency-Key` is the unscoped raw `intent_id` — cross-tenant reuse can collide in Stripe's 24h window. | Scope as `f"{creator_id}:{intent_id}"`. |
| SEV2 | analysis | `brief.py:86` | Same `cache_control` inert-breakpoint defect as `knowledge/hooks.py` + `chapters.py`. | Same fix (drop the marker, log the decision). |
| SEV2 | improvement | `brief.py:38-53` | Same `cache_control` inert-breakpoint defect (already documented in DECISIONS — verify the DECISIONS entry is still accurate and applies). | Already accepted; close the audit ticket. |

**Module verdicts** (full per-module register lives in `docs/assessment/modules/<module>.md`):

| Module | Verdict | B | S1 | S2 | C |
|---|---|---|---|---|---|
| clip_engine | NEEDS-WORK | 0 | 0 | 7 | 5 |
| dna | NEEDS-WORK | 0 | 0 | 4 | 6 |
| **preference** | **clean** | 0 | 0 | 2 | 4 |
| youtube | NEEDS-WORK | 0 | 1 | 4 | 3 |
| worker | NEEDS-WORK | 0 | 3 | 7 | 3 |
| routers | NEEDS-WORK | 0 | 0 | 9 | 9 |
| ingestion | NEEDS-WORK | 0 | 0 | 4 | 3 |
| billing | NEEDS-WORK | 0 | 0 | 1 | 3 |
| **upload_intel** | **clean** | 0 | 0 | 0 | 4 |
| improvement | NEEDS-WORK | 0 | 0 | 4 | 2 |
| knowledge | NEEDS-WORK | 0 | 2 | 4 | 5 |
| analysis | NEEDS-WORK | 0 | 0 | 5 | 3 |
| **_root_infra** | **clean** | 0 | 0 | 6 | 4 |

---

## Layer 2 — scale checklist

| Axis | Status | Evidence |
|---|---|---|
| A Pool math | ⚠️ | PgBouncer-staging stack built in Issue 112 + connection-churn fix on `/health` shipped. Locust at 300 users still NOT RUN — the user-side step from Issue 112 hasn't been executed. Needs evidence. |
| B Async loop hygiene | ⚠️ | **New finding this run**: ~15 sites in `routers/` use sync `task.delay(...)` inside `async def`. Each is a Redis round-trip blocking the loop. Cross-cutting cleanup → SEV2 individually but axis-level concern. |
| C Celery idempotency | ⚠️ | Most tasks idempotent (Issue 105 advisory locks intact). New SEV1: `clean_clip` and `edit_clip` share `cleaned_render_uri` as the conflict check; the cross-task collision is silent. |
| D Tenant isolation | ⚠️ | RLS infrastructure (Issue 79) is in place. New SEV1 finding: 2 worker async helpers (`_retrain_preference_async`, `_generate_improvement_brief_async`) don't stamp `session.info["creator_id"]` — silent empty results under role split. |
| E Backpressure | ⚠️ | Timeouts on every external client. Circuit-breaker pattern not formal but Anthropic/Voyage retries with backoff present. Locust not run — needs evidence. |
| F Rate limit / quota | ✅ | slowapi on Redis, per-creator key on every authenticated route. New `/cuts` + `/clean*` + `/transcript` endpoints all rate-limited. |
| G Observability | ✅ | Structured JSON logs, request IDs, token-usage logging on Anthropic calls (caveat: knowledge SEV1 means cache counters always log 0 on Haiku paths). |
| H Migration & pgvector safety | ✅ | Deploy workflow auto-applies migrations. All 0020 + 0021 migrations use plain non-CONCURRENTLY add_column — safe at current row count. pgvector index intact. |
| I Secrets / deletion | ✅ | Fernet tokens never logged; account-deletion endpoint + media purge intact. TOKEN_ENCRYPTION_KEY rotation runbook still listed as pre-launch TODO in CLAUDE.md. |

---

## Diff vs previous report (2026-06-07 post-Issues-130/131)

### Fixed since last run
- ✅ Issue 123 ingestion/transcribe.py singleton init races (Deepgram/AssemblyAI threadlocks) — verified intact.
- ✅ Issue 123 worker/tasks.py Redis-per-task in `_generate_thumbnail_concepts_async` — verified module-level `_thumb_redis()` singleton.
- ✅ Issue 123 worker/tasks.py `dna_profile` accessed after session close — verified eager-load inside session.
- ✅ Issue 123 `_root_infra` CreatorInsight composite index — migration `0020` applied on prod.
- ✅ Issue 123 `db.py::recreate_engine` re-entry guard — verified.
- ✅ Issue 123 youtube/analytics.py:320 `creator_id` parameter type annotation — verified.
- ✅ `ranking.py:102` defense-in-depth `creator_id` predicate addition — verified.

### New SEV1s introduced
- 🔴 worker `_clean_clip_async` + `_edit_clip_async` share `cleaned_render_uri` as idempotency key (Issue 135). Same UX limitation I flagged in DECISIONS — subagent escalated for the **silent** failure mode.
- 🔴 worker `_retrain_preference_async` + `_generate_improvement_brief_async` use `AsyncSessionLocal` without stamping `creator_id` — silent empty results under RLS role split.
- 🔴 knowledge hooks.py + chapters.py `cache_control` breakpoints inert on Haiku 4.5 (4096-token floor vs ~900-token prefix).
- 🔴 youtube `_do_token_refresh` calls `session.commit()` inside caller-owned session — flushes unrelated pending writes.

### Carry-forward (unchanged from prior run)
- ⚠️ `preference/train.py` advisory lock acquired after `fit()` (concurrent retrains waste CPU; correctness intact). Demoted from SEV1 → SEV2 by the new subagent — re-classification, not a fix.

### New SEV2 cluster (cross-cutting)
- 🟡 `routers/` ~15 sync `task.delay(...)` inside `async def` — axis-B violation. Not present in prior run because the subagent's focus has shifted to deep-async-hygiene this cycle.

---

## Top 5 actions, in order

1. **`worker/tasks.py:874+1006` — fix the clean/edit silent-collision SEV1.** Either add a separate `Clip.edited_render_uri` column (migration 0022, 1 line) or short-circuit the second task with a 409-style error to the SSE stream instead of "done". Cost: ~1 hour.
2. **`worker/tasks.py:346+1978` — stamp `creator_id` on the two async helpers OR switch to `AdminSessionLocal`.** Pick one per task: retrain → `AdminSessionLocal` (it's a worker-internal pass); improvement-brief → stamp `session.info["creator_id"]` because the brief query already has `WHERE creator_id == cid` and would benefit from belt-and-suspenders. Cost: ~30 min.
3. **`knowledge/hooks.py:176` + `knowledge/chapters.py:182` — drop the inert `cache_control` breakpoint** and add a one-paragraph DECISIONS entry matching the existing improvement/brief.py precedent. Cost: ~15 min. Also touch `analysis/brief.py:86`.
4. **`youtube/oauth.py:243,258` — stop committing the caller's session.** Open an internal session for the token write OR document that callers must commit before invocation. Cost: ~30 min (touches every callsite if we pick the API change).
5. **Routers axis-B cross-cutting — wrap the 15 sync `task.delay(...)` calls in `await asyncio.to_thread(...)`.** Extract a helper `await _enqueue(task, *args)` to keep the routers slim. Cost: ~1 hour for the sweep + tests. **Then** schedule the deferred Locust 300-user run to close axes A and E with evidence.

After the SEV1s and the routers axis-B sweep land, this jumps to **PRODUCTION-READY: YES** subject to a single Locust run.
