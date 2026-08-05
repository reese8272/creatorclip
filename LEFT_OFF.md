# LEFT_OFF.md — CreatorClip Session Handoff

**Last updated:** 2026-08-05 · **Branch:** `main` @ `6c3cf4f` · working tree clean · in sync with `origin/main`; `staging` synced
**Prod:** `https://autoclip.studio` — **L26 IS LIVE**: deploy run 30971145148 green, `/health` 200, **prod DB at head `0055`** (verified on the VM), LLM E2E Nightly green post-deploy (run 30979325939).
**Local branches:** only `main` — all `lane/l26-*` branches and agent worktrees removed after merge.

> Source-of-truth docs live in `docs/`. This file orients and points to them — it is NOT a source of truth.

---

## CURRENT FOCUS

**Lane L26 — A→B Auto-Clipping MVP (Issues 414–426) — SHIPPED TO PROD 2026-08-05** (merge `9eb5217`
+ deploy-unblock fix `6c3cf4f`). What's live: whole-video LLM context pass feeding hybrid
LLM ∪ signal clip candidates; every clip auto-packaged with suggested title/description/hook;
diarized transcripts; the short-first unified ShortStage UI (Review media 1.89×); crop-track
API + overlay. What's built but NOT live: the dynamic speaker-following crop —
`ACTIVE_SPEAKER_REFRAME_ENABLED=false` until the staging checklist closes.

## → NEXT ACTIONS (in order)

1. **User stress-test of the live A→B flow** (user said they'd do this before more feedback):
   upload a real long-form video → SSE stages `video_context` → clips → `metadata_ready` → every
   clip should arrive titled/described/hooked, Review short front-and-center. Eyeball: `origin: llm`
   clips on story-heavy flat-energy videos; titles grounded in the right window. Feedback → issues
   in the L26 lane.
2. **Dynamic crop staging verification (Issue 422)** — `docs/DEPLOYMENT.md` § "Speaker-Aware
   Reframe — Staging Rollout Checklist". **Step 0 BLOCKS everything**: `mediapipe==0.10.21` pins
   `numpy<2` vs the app's `numpy==2.1.3` → the image only installs it via
   `docker build --build-arg INSTALL_REFRAME=true` (default false; this failed the first
   docker-publish and was hotfixed in `6c3cf4f`). Evaluate in order: newest mediapipe (≥0.10.31 —
   check numpy pin lifted AND Tasks `FaceDetector` still ships), patched wheel
   (cansik/mediapipe-numpy2 style), dedicated numpy<2 worker image (last resort). Then the four
   Issue-189 unlock criteria with evidence → flag flip staging→prod → DECISIONS reversal entry.
3. **Optional cosmetic**: refresh non-asserted Review/Editor smoke screenshots —
   `gh workflow run ci.yml -f update_snapshots=true --ref main` → download `visual-baselines-<sha>`
   → commit. The 6 asserted baselines were untouched by L26 and pass.
4. **Cost watch:** +≈$0.11–0.16 LLM/video; Deepgram now $0.0097/min (diarization add-on,
   `PRICE_BOOK_VERSION` 2026-08-04). Kill-switches (each restores prior behavior, test-pinned):
   `VIDEO_CONTEXT_ENABLED=false` · `AUTO_CLIP_METADATA=false` · `TRANSCRIPTION_DIARIZE_ENABLED=false`.
5. **Then resume L25 Batch C** (393 next; 394/396 re-scoped to the ShortStage seams per DECISIONS) ·
   operator punch-list unchanged (`docs/GO_LIVE.md`): #29 OAuth verification · #26/#28 friend beta ·
   #282 uptime monitor · #255 key escrow.

### Local gate incantations

```bash
export PATH=/home/reese/.nvm/versions/node/v22.17.1/bin:$PATH && hash -r   # `nvm use 22` LIES in this shell — node 26 breaks jsdom (35 phantom vitest failures)
redis-cli ping || redis-server --daemonize yes --save '' --appendonly no
# backend:  .venv/bin/python -m pytest -m "not integration" -p no:langsmith -q
# frontend: run from frontend/ (root npx = cached-Playwright ERR_INTERNAL_ASSERTION)
```

---

## WHAT WORKS NOW (verified on the shipped state — do not re-investigate)

| Gate | Value |
|---|---|
| Backend unit lane | **2766 passed / 71 skipped / 0 failed** (merged tree) |
| Eval harness | **22/22**, `SCENARIO_FLOOR=18`, `extract_candidates` byte-untouched (tripwire test) |
| Alembic | single head **0055** (0053 video_context · 0054 suggested metadata · 0055 crop track); **prod DB migrated + verified** |
| Frontend vitest (node 22) | **624 passed / 86 files** (six structural gates included) |
| `tsc -b` · lint · build | clean · 0 errors (1 pre-existing warning `useStageStream.ts`) · clean |
| Playwright e2e (local) | **83 / 0 / 11** incl. editor-persistence CAS, a11y, geometry, new ≥1.8× media-box assertion |
| Deploy chain | docker-publish ✅ (post-fix) → deploy ✅ → `/health` 200 → LLM E2E Nightly ✅ |
| ruff | `check` clean; `format` clean (13-file format pass `3b0ee57` — CI gates on `--check`) |

Known pre-existing failure that is NOT ours: `tests/test_response_models.py::test_every_documented_json_route_declares_response_model`
under an alternate pytest invocation — fails on pristine pre-L26 base too; off-course-logged.

---

## THE ARC THAT LED HERE

1. User declared the MVP bar: "best auto-clipping short creation on the planet" — three verified
   gaps: LLM never read the video, crop was one frozen midpoint face, short was ~9% of Review.
2. Plan session (2026-08-04): 3 explore + 3 design agents → approved 3-track plan; user chose
   speaker-aware pan+cuts, auto-metadata for ALL clips, unified Review+Editor stage.
3. Lane L26 filed (issues 414–426, 10 DECISIONS), built as three parallel worktree agents on lane
   branches, merged serially (intel → crop w/ 0055 re-parent → stage), all local gates green.
4. Merge to main: first docker-publish failed on the mediapipe×numpy conflict → gated behind
   `INSTALL_REFRAME` build arg (`6c3cf4f`) → deploy green, prod DB 0055, L26 live.

---

## KEY COORDINATES & FACTS

| Thing | Value |
|---|---|
| Repo | `github.com/reese8272/creatorclip` |
| Production | `https://autoclip.studio` — VM (`ssh creatorclip-vm`, standing permission) + docker-compose (`autoclip-*` containers) + Cloudflare tunnel |
| Deploy chain | push to `main` → `docker-publish.yml` → `deploy.yml` (staging gate → prod). **A failed image build silently SKIPS deploy** — prod stays on the old image; check `gh run list` after every push |
| Staging sync | after any merge to `main`: `git push origin main:staging` (`docs/BRANCHING.md`) |
| Reframe image | `docker build --build-arg INSTALL_REFRAME=true` (default false); model asset baked at `MEDIAPIPE_FACE_MODEL_PATH=/usr/share/mediapipe-models/blaze_face_short_range.tflite` |
| Alembic head | **`0055_clip_reframe_track`** — local AND prod |
| Crop-track API | `GET /clips/{id}/crop-track` (404 `no_crop_track`); `x` = clamped LEFT edge in source px = exact sendcmd values; cuts are value jumps; computed track on `clips.reframe_track_jsonb`, NEVER the EditDocument (CAS conflict — #396 layers there later) |
| Metadata semantics | pipeline writes `suggested_*` only; `applied_*` creator-typed; publish falls back `applied → suggested → (video.title \| "#Shorts")` |
| New config keys | all in `.env.example` with working defaults — no VM `.env` edit was needed |
| Node for local gates | `/home/reese/.nvm/versions/node/v22.17.1/bin` on PATH + `hash -r` (`nvm use` alone does not stick) |
| Python | `.venv/bin/python` (system `python3` lacks pydantic; user-site has stale FastAPI) |
| Layer 0 | `.venv/bin/python .claude/skills/production-assessment/scripts/run_layer0.py` — repo root |
| Secrets | `.env` on the VM; rotation runbook `docs/RUNBOOKS.md`. **Names only — never values.** |

---

## CONSTRAINTS & GOTCHAS (carried forward — all still true)

- **`analyze_video_context` can never raise** — LLM failure → `context_skipped` SSE step → clips
  generate signal-only. If clips look signal-only, check for that event before suspecting the merge.
- **`extract_candidates` must stay byte-identical** — the eval harness's green-by-construction
  argument rests on it; the hybrid merge is a separate pure layer (`clip_engine/merge.py`).
- **Speaker→track mapping is per-shot** (`(shot_idx, speaker)`) — tracks can't span camera cuts.
- **Track C's sizing excludes the trim filmstrip on purpose** (below the fold in the scrollable
  stage cell at 1440×900) — "fixing" it caps media at ~1.45× and fails the 1.8× gate.
- Run frontend commands from `frontend/`; chain gates with `&&` never `;`; piped `| tail` eats
  non-zero exits; `ruff format` reflows between Edits; backticks in `git commit -m` = command
  substitution (heredoc); CI runs `ruff format --check`; visual baselines are ubuntu-CI-only;
  Playwright `--workers=1` on this box after heavy runs; the harness shell CWD can drift (a `cd
  frontend` sticks — `cd` back to repo root before git ops; bit this session).
- Six structural gates in `frontend/src/test/` fail `npm test`; honesty copy pinned — don't "tidy".
- Postgres GUC: always `NULLIF(current_setting('app.creator_id', true), '')::uuid`.
- Integration/RLS tests need Docker (absent locally) — CI-verified only; say so plainly.

---

## OPEN, LOGGED, NOT FIXED

Canonical list is `docs/OFF_COURSE_BUGS.md`. Most likely to matter first:

1. **Prod migrations run with NO pre-migration safety dump** (`BACKUP_R2_BUCKET` unset, Issue 256).
   L26's 0053–0055 were expand-only and cleared the bar — set the bucket before the first
   destructive migration.
2. **`clips/` storage is not creator-scoped** → `DELETE /auth/me` purge matches nothing
   (right-to-erasure gap).
3. Roving tabindex on the timeline · Review-queue badge counts rendered clips · `App.tsx` `*`
   route silently redirects typos · `ORIGINALITY_SIMILARITY_THRESHOLD=0.92` unvalidated · export
   collector omits ~10 tables · e2e mock's known unmodeled GETs (`/api/notifications`,
   `/clips/c1/download`) · `tests/test_response_models.py` peaks/stream route-model gap (pre-existing).

---

## POINTERS

| Doc | Purpose |
|---|---|
| `docs/issues.md` | Work queue — **§ Lane L26: all closed except 422's staging boxes → then L25 Batch C (393)** |
| `docs/PROJECT_STATE.md` | Progress log — top entries: L26 declaration + build close-outs |
| `docs/DECISIONS.md` | 2026-08-04 entries: L26 10-decision block · Track A deviations · Deepgram surcharge · Track C deviations + merge notes |
| `docs/DEPLOYMENT.md` | § Speaker-Aware Reframe — Staging Rollout Checklist (**NEXT ACTION 2**, step 0 = numpy conflict) |
| `docs/SOT.md` | Stack, schema, structure — updated for VideoContext / crop track / stage components |
| `docs/OFF_COURSE_BUGS.md` · `docs/GO_LIVE.md` | Logged defects · operator launch scorecard |
| `docs/BRANCHING.md` · `docs/MIGRATIONS.md` · `docs/CLIPPING_PRINCIPLES.md` | Promotion model · migration templates · principles registry |
| `~/.claude/ISSUES_LOG.md` | Cross-project incident log |
| Memory dir | `/home/reese/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` (`project_l26_autoclip_mvp.md` is the lane memory) |
