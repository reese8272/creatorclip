# LEFT_OFF — Session Handoff Contract

> **Read this first.** Living "where we are right now" file. Not a changelog, not a
> source of truth — those live in `docs/`. Updated at the end of every session.

**Last updated:** 2026-06-07 (Issue 127 — sentence-boundary cuts + Creator Studio backlog)
**Branch:** `claude/competitive-app-analysis-DtPQN` — HEAD `7c7dbf6`
**Sync with `origin/claude/competitive-app-analysis-DtPQN`:** up to date
**Working tree:** CLEAN
**CI (last 5 runs):** see repo Actions tab

---

## CURRENT FOCUS

### Issue 127 closed. Next: Issue 128 — Title optimizer

Issue 127 is fully shipped and closed out. The next highest-ROI item in the queue is
Issue 128 (Title optimizer): `POST /creators/me/videos/{video_id}/titles` → Celery task →
5 Claude-generated title candidates, channel-voice-aware + web_search grounded, CTR signal.

### → NEXT ACTION

**Kick off Issue 128 via the issue workflow.**

Read `docs/issues.md` Issue 128 spec, then open Phase 1 CHECK:

```
Issue 128 — Title optimizer
Approach: Celery task, AsyncAnthropic with prompt caching + web_search tool, 5 ranked
candidates with CTR rationale, SSE stream_url returned immediately (202 pattern mirrors
DNA build / improvement brief).
Why: Daily-use feature; keeps creators inside CreatorClip instead of switching to
ChatGPT. Highest time-to-value of the remaining Creator Studio items.
Industry standard checked: [research in Phase 1]
Good to go?
```

Use `/claude-api` skill before writing any Anthropic SDK code (mandatory per CLAUDE.md).

---

## WHAT WORKS NOW (do not re-investigate)

### This session (2026-06-07)

- **Competitive intelligence analysis (`docs/other_apps_research.md` uploaded):**
  - Confirmed winning wedge: YouTube-stream-native + style-learning (vs commodity repurposing)
  - #1 complaint about Opus Clip: mid-sentence cuts → directly addressed by Issue 127
  - Identified 9 new Creator Studio features (Issues 128–136) ordered by ROI

- **Issues 127–136 added to backlog (`docs/issues.md` + `docs/PROJECT_STATE.md`):**
  - 127: Sentence-boundary cut enforcement ✅ Done
  - 128: Title optimizer (next)
  - 129: Thumbnail concept generator
  - 130: Hook analyzer
  - 131: Auto chapter markers
  - 132: YouTube Live Chat spike detection
  - 133: Animated caption styles
  - 134: Filler word and silence removal
  - 135: Text-based editor
  - 136: UI upgrade — dark editor mode + marketing hero

- **Issue 127 — Sentence-boundary cut enforcement (committed + pushed `a15e9a0`):**
  - `clip_engine/candidates.py` — `_is_sentence_end()` + `snap_to_sentence_boundary(ts, words, direction)`:
    walks word-level timestamps for terminal-punct tokens (`.?!…`), silence-gap fallback (≥400ms),
    3-second hard cap (`MAX_SNAP_S`). `extract_candidates()` now accepts `words` kwarg and snaps
    both clip endpoints after NMS with setup/peak/end invariant preserved.
  - `clip_engine/ranking.py` — flattens `transcript_segments[].words` and passes into `extract_candidates`
    via `lambda` (required for `asyncio.to_thread` + kwargs)
  - `clip_engine/scoring.py` — `_transcript_context(setup_s, end_s, segments)` replaces the old
    300-char `_transcript_excerpt`. Returns `[BEFORE 60s] / [CLIP] / [AFTER 30s]` three-section window
    so Claude can judge whether each cut point falls on a complete thought. Payload field renamed
    `transcript_context`.
  - `ingestion/signals.py` — `RetentionCurve.is_rewatch_spike` fires `retention_spike` event
    unconditionally (was gated behind `relative_retention_performance > 1.2`). This is the same
    data YouTube surfaces as "most replayed" — now always used as a direct clipping anchor.
  - `config.py` + `.env.example` — `SENTENCE_BOUNDARY_MIN_PAUSE_MS=400`, `MAX_SNAP_S=3.0`
  - `docs/CLIPPING_PRINCIPLES.md` — Principle #12 (Clean Context Boundary) added
  - `docs/DECISIONS.md` — 2026-06-07 entry: punctuation-token walk over spaCy/NLTK; `is_rewatch_spike`
    as direct trigger; three-section context transcript
  - **Tests:** 704 passed (+13 from 691) / 2 skipped. Layer 0: ruff 0 / mypy 0.

- **Close-out commit (pushed `7c7dbf6`):**
  - `docs/issues.md` — Issue 127 marked ✅ Done (2026-06-07)
  - `docs/PROJECT_STATE.md` — full completion summary added at top; queued entry struck through

### Longer-standing landmarks (verified, do not re-check)

- **Issue 124 (performance score + hover tooltips):** deployed; `performance_score` (0–100) on
  `PerformerOut`; `static/tooltip.js` reusable tooltip component on all authenticated pages
- **Production live:** `https://autoclip.studio` healthy
- **Alembic head:** `0019_clip_style_preset` — prod and local in sync
- **RLS** on 12 tenant-owned tables; `creators` table deliberately exempt
- **Self-hosted runner deploy pipeline:** push to `main` → Docker publish → deploy
- **Stripe billing, OBS API key surface, walkthrough gate, design system** — all deployed
- **Issue 122 rate-limiter gap:** `POST /api/activity` has no rate limiter (carried forward
  from last session — still unresolved if not already patched on `main`)

---

## THE ARC THAT LED HERE

1. User uploaded `docs/other_apps_research.md` — competitive intelligence (Opus Clip, Captions/Mirage).
2. Analysis: market commoditized at general repurposing; stream-native + style-learning is the wedge;
   mid-sentence cuts are Opus's #1 complaint; offline analysis has fundamentally better quality ceiling.
3. Issues 127–136 filed in ROI order — functionality first.
4. Issue 127 Phase 1 (CHECK) → Phase 2 (APPROVE) → Phase 3 (BUILD) → Phase 4 (REVIEW) — all green.
5. Close-out: `docs/issues.md` + `docs/PROJECT_STATE.md` updated; committed and pushed.

---

## KEY COORDINATES & FACTS

| Item | Value |
|---|---|
| Public URL | `https://autoclip.studio` |
| Production VM | `147.182.136.107` |
| Container image | `ghcr.io/reese8272/creatorclip:latest` |
| Repo | `github.com/reese8272/creatorclip` |
| Self-hosted runner | systemd `actions.runner.reese8272-creatorclip.autoclip-prod-vm` on prod VM |
| Current branch | `claude/competitive-app-analysis-DtPQN` |
| HEAD | `7c7dbf6` |
| Alembic head | `0019_clip_style_preset` |
| Next migration | `0020_creator_insight_index` (Issue 123 SEV1 sweep — still pending) |
| Issue 127 | ✅ Done — sentence-boundary cuts + three-section context + rewatch spike anchor |
| Issue 128 | 🔲 Not started — Title optimizer (next in queue) |
| Test count | 704 passed / 2 skipped (Layer 0: ruff 0 / mypy 0) |
| Default model | `claude-sonnet-4-6` (Sonnet 4.6) |
| Secret names (never log) | `STRIPE_SECRET_KEY`, `JWT_SECRET_KEY`, `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, `GOOGLE_OAUTH_CLIENT_SECRET`, `TOKEN_ENCRYPTION_KEY`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `GHCR_TOKEN`, `DEEPGRAM_API_KEY` |

---

## CONSTRAINTS & GOTCHAS (next session: read before acting)

- **`claude/competitive-app-analysis-DtPQN` branch is NOT merged to `main`.** All Issue 127
  work lives on this branch. Decide whether to merge or continue building 128+ here before pushing
  to production.
- **Pushing `main` auto-deploys to production.** Self-hosted runner: Docker publish → deploy.
  No staging gate. Each push = a production cut.
- **Issue 123 (SEV1 sweep) still pending.** 5 open SEV1s: `analyze_performer` singleton,
  ingestion threading races (×2), `CreatorInsight` missing index, `recreate_engine` re-entry guard.
  These are pre-production blockers. See `docs/assessment/REPORT.md`.
- **`ruff format --check` is a CI gate; Layer 0 only runs `ruff check`.** Always run
  `ruff format .` before pushing or CI lint fails even if `ruff check` passed locally.
- **Integration tests always fail in CI** — need live Postgres; CI doesn't provision one.
  Does NOT block deploy. Do not change CI config to fix this.
- **`tests/_helpers.py::override_current_creator`** must be used instead of `lambda: creator`
  in ALL test dependency overrides for `get_current_creator`.
- **`snap_to_sentence_boundary` is a no-op when `words=None`** (backward-compat default). Always
  pass `words` from `transcript_segments[].words` or snapping silently does nothing.
- **`is_rewatch_spike` fires unconditionally now.** If `RetentionCurve.is_rewatch_spike` is `True`,
  a `retention_spike` event is always emitted regardless of `relative_retention_performance`.
- **OAuth tokens Fernet-encrypted at rest.** Read via `decrypt()`; never log.
- **Per-creator `WHERE` on every query.** Missing filter = BLOCKER (RLS is backstop, not substitute).
- **`YOUTUBE_ANALYTICS_MAX_STALENESS_DAYS=30`** is a YouTube ToS hard limit. Do NOT increase.
- **`LOCAL_MEDIA_DIR` validator is relaxed** (Issue 110 hotfix): only fails fast in production
  when `STORAGE_BACKEND=local`. Do NOT revert.

---

## POINTERS

- `docs/SOT.md` — current stack, file structure, data model
- `docs/PROJECT_STATE.md` — every issue's status + session log
- `docs/issues.md` — backlog (Issues 127–136 queued; 127 done)
- `docs/DECISIONS.md` — deviation log (2026-06-07 entry for Issue 127)
- `docs/COMPLIANCE.md` — YouTube ToS, retention, privacy posture
- `docs/CLIPPING_PRINCIPLES.md` — named principles registry (Principle #12 added)
- `docs/assessment/REPORT.md` — last `/assess` verdict (post Issues 120–122; Issue 123 SEV1s still open)
- `docs/other_apps_research.md` — competitive intelligence report (uploaded 2026-06-07)
- `CLAUDE.md` — project rules; the One Rule is non-negotiable
