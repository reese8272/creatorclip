# LEFT_OFF — Session Handoff Contract

> **Read this first.** Living "where we are right now" file. Not a changelog, not a
> source of truth — those live in `docs/`. Updated at the end of every session.

**Last updated:** 2026-06-07 (Issue 128 — Title optimizer)
**Branch:** `main` — HEAD `4dfbf05` (already on origin/main)
**Working tree:** DIRTY — Issue 128 is complete but NOT yet committed/pushed
**CI (last push):** CI + Quality Gates failed on PR #17 merge; Deploy succeeded (production live)

---

## CURRENT FOCUS

### Issue 128 done. Commit + push it, then start Issue 129.

All Issue 128 code is written, tested, and ruff-clean — sitting in the working tree uncommitted.
**722 passed / 2 skipped. ruff 0. ruff format 0. Layer 0 all-runnable gates pass.**

The next session has two actions: ship Issue 128 (commit + push), then kick off Issue 129
(Thumbnail concept generator) via the issue workflow.

### → NEXT ACTION

**Step 1 — Commit and push Issue 128:**

```bash
git add knowledge/titles.py routers/titles.py tests/test_titles.py \
        main.py worker/tasks.py static/analysis.html static/index.html \
        docs/DECISIONS.md docs/PROJECT_STATE.md docs/SOT.md docs/issues.md \
        clip_engine/candidates.py clip_engine/scoring.py tests/test_scoring.py
git status   # verify only Issue 128 files staged
git commit -m "feat(128): title optimizer — 202+SSE, 5 channel-voice-aware candidates"
git push     # auto-deploys to production — confirm deploy CI passes
```

**Step 2 — Start Issue 129 via the issue workflow:**

```
Issue 129 — Thumbnail concept generator
Approach: POST /creators/me/videos/{video_id}/thumbnail-concepts → Celery task →
3-5 structured thumbnail concept briefs, channel-pattern analysis + web_search grounded.
Mirrors Issue 128 endpoint pattern. Concepts only — no image rendering.
Industry standard checked: [research in Phase 1]
Good to go?
```

Use `/claude-api` skill before writing any Anthropic SDK code (mandatory per CLAUDE.md).

> **CI note:** Quality Gates and CI have been failing on pushes to `main` since the last
> PR merge. Before pushing, run `.venv/bin/ruff check .` and `.venv/bin/ruff format --check .`
> locally — both must be clean. Integration tests always fail in CI (no live Postgres) — that
> does NOT block the deploy.

---

## WHAT WORKS NOW (do not re-investigate)

### This session (2026-06-07) — Issue 128

- **`POST /creators/me/videos/{video_id}/titles`** (20/hour, auth required):
  - Returns 202 + `{task_id, stream_url, video_title}`
  - Guards: video must exist and belong to the creator; video must have been transcribed
  - Rate limit: `@limiter.limit("20/hour", key_func=creator_key)`

- **Celery task `generate_title_suggestions`** in `worker/tasks.py`:
  - `_generate_title_suggestions_async(job_id, creator_id, video_id)` fetches Transcript,
    DNA brief, and stated identity from DB using `AdminSessionLocal`
  - Calls `asyncio.to_thread(build_suggestions, ..., task_id=job_id)` — streams tokens via SSE
  - Parses Claude's JSON response (10 candidates) → surfaces top 5 via `parse_candidates`
  - Results passed in the `done` event payload (`suggestions` key) — no DB row persisted

- **`knowledge/titles.py`** — three-block prompt:
  - Block 1: static CTR instructions (no cache_control)
  - Block 2: DNA brief — carries `cache_control: {type: ephemeral}` (combined ~2400 tokens,
    clears Sonnet 4.6's 2048-token cache minimum)
  - Block 3: per-video context (transcript summary + stated identity) — uncached
  - `parse_candidates(raw_json)` enforces 100-char limit, normalizes bad CTR signals to "neutral"
  - `_extract_transcript_summary(segments_jsonb)` extracts plain text from `segments_jsonb["segments"]`

- **`static/analysis.html`** — Title Optimizer panel:
  - Auto-shown when `?video_id=<uuid>&video_title=<encoded>` query params are present
  - "Generate titles" button → SSE stream → renders title cards with CTR badge + copy button
  - Disclaimer uses "cannot guarantee" (not "promise") — passes the structural virality scan

- **`static/index.html`** — "Titles" link button on every `ingest_status=done` video row,
  links to `/static/analysis.html?video_id=<id>&video_title=<encoded>`

- **18 new tests** in `tests/test_titles.py`: prompt structure (3 blocks, cache on block 2),
  web_search tool presence, `parse_candidates` (valid, bad CTR signal, 100-char limit, invalid
  JSON, missing key), `_extract_transcript_summary` (None, empty, join, max_chars), API
  (auth required, 404 isolation, 400 no-transcript, 202 happy path with task queued)

- **Key decisions logged in `docs/DECISIONS.md`** (2026-06-07 entry):
  - Ephemeral (no DB table) instead of persistent like improvement_briefs — no migration needed
  - Generate 10, surface 5 (per Phase 2 user approval)
  - CTR signal as UI label only with explicit ±0.5% band definition
  - cache_control at DNA-brief block (not static instructions) to clear 2048-token minimum
  - Sync Anthropic + asyncio.to_thread (not AsyncAnthropic — wait for Issue 82)

### Longer-standing landmarks (verified, do not re-check)

- **Branch situation:** `claude/competitive-app-analysis-DtPQN` was merged to `main` via
  PRs #16 and #17 — all Issue 127 work is on `main`
- **Issue 127 (sentence-boundary cuts):** deployed; `snap_to_sentence_boundary`, three-section
  context transcript, `is_rewatch_spike` unconditional trigger — all in `clip_engine/`
- **Issue 124 (performance score + hover tooltips):** deployed; `performance_score` on `PerformerOut`
- **Production live:** `https://autoclip.studio` healthy
- **Alembic head:** `0019_clip_style_preset` — prod and local in sync (no migration in Issue 128)
- **RLS** on 12 tenant-owned tables; `creators` exempt
- **Self-hosted runner deploy pipeline:** push to `main` → Docker publish → deploy
- **Stripe billing, OBS API key surface, walkthrough gate, design system** — all deployed

---

## THE ARC THAT LED HERE

1. Competitive intelligence analysis (`docs/other_apps_research.md`) → identified market wedge:
   stream-native + style-learning. Mid-sentence cuts were Opus Clip's #1 complaint.
2. Issues 127–136 filed in ROI order. 127 shipped: sentence-boundary cut enforcement.
3. Issue 128: phase 1 research (workflow: parallel YouTube CTR + Claude API agents) → phase 2
   approval (generate 10/surface 5, 20/hour, CTR label only) → phase 3 build → phase 4 review.
4. 18 tests written alongside code. Full suite: 722 passed. ruff + format clean.
5. `LEFT_OFF.md` written. Commit + push is the only remaining action.

---

## KEY COORDINATES & FACTS

| Item | Value |
|---|---|
| Public URL | `https://autoclip.studio` |
| Production VM | `147.182.136.107` |
| Container image | `ghcr.io/reese8272/creatorclip:latest` |
| Repo | `github.com/reese8272/creatorclip` |
| Self-hosted runner | systemd `actions.runner.reese8272-creatorclip.autoclip-prod-vm` on prod VM |
| Current branch | `main` |
| HEAD | `4dfbf05` (origin/main in sync) |
| Working tree | DIRTY — Issue 128 uncommitted |
| Alembic head | `0019_clip_style_preset` |
| Issue 128 | ✅ Written, tested, NOT committed yet |
| Issue 129 | 🔲 Not started — Thumbnail concept generator (next in queue) |
| Test count | 722 passed / 2 skipped (Layer 0: ruff 0 / format 0 / freshness ok) |
| Default model | `claude-sonnet-4-6` (Sonnet 4.6) |
| Secret names (never log) | `STRIPE_SECRET_KEY`, `JWT_SECRET_KEY`, `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, `GOOGLE_OAUTH_CLIENT_SECRET`, `TOKEN_ENCRYPTION_KEY`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `GHCR_TOKEN`, `DEEPGRAM_API_KEY` |

---

## CONSTRAINTS & GOTCHAS (next session: read before acting)

- **Issue 128 is uncommitted on `main`.** Run the commit + push in Step 1 before anything else.
  Pushing `main` auto-deploys to production — no staging gate. Verify CI passes after push.
- **CI Quality Gates have been failing since PR #17 merge.** Check `gh run list` after pushing
  to confirm deploy succeeded despite any CI gate failures (deploy is on a separate workflow).
- **Integration tests always fail in CI** — need live Postgres. Does NOT block deploy.
- **`ruff format --check` is a CI gate.** Always run `ruff format .` before pushing or CI lint
  fails even if `ruff check` passed locally. Already done for Issue 128.
- **Issue 123 (SEV1 sweep) still pending.** 5 open SEV1s: `analyze_performer` singleton,
  ingestion threading races (×2), `CreatorInsight` missing index, `recreate_engine` re-entry guard.
  Pre-production blockers. See `docs/assessment/REPORT.md`.
- **Issue 122 rate-limiter gap:** `POST /api/activity` has no rate limiter (still unresolved).
- **`snap_to_sentence_boundary` is a no-op when `words=None`** — always pass from
  `transcript_segments[].words` or snapping silently does nothing.
- **OAuth tokens Fernet-encrypted at rest.** Read via `decrypt()`; never log.
- **Per-creator `WHERE` on every query.** Missing filter = BLOCKER (RLS is backstop, not substitute).
- **`YOUTUBE_ANALYTICS_MAX_STALENESS_DAYS=30`** is a YouTube ToS hard limit. Do NOT increase.
- **Title suggestion results are ephemeral** — they arrive in the SSE `done` payload, not a DB row.
  If a creator navigates away during generation, they re-generate. This is by design (Issue 128
  DECISIONS.md). Do not add DB persistence without a new issue + migration.
- **`_extract_transcript_summary` reads `segments_jsonb["segments"]`** (dict, not bare list).
  The `Transcript.segments_jsonb` column is always a dict with a `"segments"` key.

---

## POINTERS

- `docs/SOT.md` — current stack, file structure, data model
- `docs/PROJECT_STATE.md` — every issue's status + session log
- `docs/issues.md` — backlog (Issues 128 done; 129–136 queued)
- `docs/DECISIONS.md` — deviation log (2026-06-07 entry for Issue 128)
- `docs/COMPLIANCE.md` — YouTube ToS, data retention, privacy posture
- `docs/CLIPPING_PRINCIPLES.md` — named principles registry (Principle #12 added in Issue 127)
- `docs/assessment/REPORT.md` — last `/assess` verdict (Issue 123 SEV1s still open)
- `docs/other_apps_research.md` — competitive intelligence report
- `CLAUDE.md` — project rules; the One Rule is non-negotiable
