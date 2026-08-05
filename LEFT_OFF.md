# LEFT_OFF.md — CreatorClip Session Handoff

**Last updated:** 2026-08-04 (late night) · **Branch:** `main` @ the L26 merge · **working tree clean**
**Prod:** `https://autoclip.studio` — push-to-main auto-deploys (`docker-publish.yml` → `deploy.yml`, staging gate → prod).
**Prod DB head before this merge: `0052`** — this merge carries **0053 + 0054 + 0055** (all expand-only: one new table, five nullable columns — no rewrites/drops, so safe under the no-pre-migration-dump caveat logged in OPEN items).

> Source-of-truth docs live in `docs/`. This file orients and points to them — it is NOT a source of truth.

---

## CURRENT FOCUS

**Lane L26 — A→B Auto-Clipping MVP (Issues 414–426) — BUILT, MERGED TO MAIN, all local gates green.**
Three tracks built in parallel lane branches and merged serially (intel → crop → stage):
- **A (414–417):** whole-video LLM context pass (`VideoContext`, never-fail chain member) → hybrid
  LLM ∪ signal candidates → ONE batched call giving every clip a suggested title/description/hook.
- **B (418–422):** Deepgram diarization (additive `speaker` fields) → ffmpeg scdet shots →
  speaker→face voting + cut/pan planner → persisted crop track + `GET /clips/{id}/crop-track`.
  **Reframe stays OFF in prod** (`ACTIVE_SPEAKER_REFRAME_ENABLED=false`) pending the staging checklist.
- **C (423–426):** shared ShortStage; Review media box **1.89×** (test-enforced ≥1.8×); Editor flip;
  compact ClipCase/ClipMetadataPanel; CropTrackOverlay mini-map.

Design record: approved plan (2026-08-04 session) · `docs/DECISIONS.md` 2026-08-04 entries ·
`docs/issues.md` § L26.

## → NEXT ACTIONS (the user's remaining punch-list)

**What is LIVE after this deploy with zero further action:** whole-video context, LLM-proposed
clips, auto titles/descriptions/hooks, the new Review/Editor UI, diarized transcripts. What is
NOT live yet: the dynamic speaker-following crop (flag off until step 1 closes).

1. **Turn on the dynamic crop — staging verification (Issue 422; the only code-adjacent item).**
   Checklist + 5-step flag sequencing + rollback: `docs/DEPLOYMENT.md` § "Speaker-Aware Reframe —
   Staging Rollout Checklist". Summary: build the worker image with `requirements-image.txt`
   (adds mediapipe), verify BlazeFace + model asset load; enable `ACTIVE_SPEAKER_REFRAME_ENABLED=true`
   with `REFRAME_CUT_ENABLED=false` on staging, verify the pan rung; enable cuts, upload a real
   2-speaker video, confirm the crop follows the speaker; record timings (budget est. +12–24s per
   60s clip); check sendcmd tmp cleanup; then flip prod and write the Issue-189 reversal into
   `docs/DECISIONS.md` with the evidence. Issue 422's acceptance boxes in `docs/issues.md` stay
   open until then.
2. **After merge CI is green, optionally refresh smoke screenshots:**
   `gh workflow run ci.yml -f update_snapshots=true --ref main` → download the
   `visual-baselines-<sha>` artifact into `frontend/e2e/` → commit. The 6 *asserted* baselines
   (login/pricing/dashboard) were untouched by L26 and pass — this dispatch only refreshes the
   non-asserted Review/Editor smoke shots, so it's cosmetic bookkeeping, not a gate.
3. **Stress-test the A→B flow yourself (you said you would):** upload a real long-form video at
   autoclip.studio → watch the pipeline (new SSE stages `video_context` → clips → `metadata_ready`)
   → every clip should arrive with suggested title/description/hook and the Review page should
   put the short front-and-center. Things to eyeball: does the LLM surface a flat-energy story
   (`origin: llm` clips exist on story-heavy videos)? Are titles grounded in the right part of the
   video (the 1500-char grounding bug is fixed)? Feedback → file as issues in the L26 lane.
4. **Cost watch:** ~$0.11–0.16 added LLM cost per video (context + metadata) and Deepgram is now
   $0.0097/min (diarization add-on verified on deepgram.com/pricing; `PRICE_BOOK_VERSION`
   2026-08-04). Kill-switches if anything misbehaves: `VIDEO_CONTEXT_ENABLED=false`,
   `AUTO_CLIP_METADATA=false`, `TRANSCRIPTION_DIARIZE_ENABLED=false` — each restores prior
   behavior exactly (byte-compat pinned by tests).
5. **Unchanged operator punch-list** (`docs/GO_LIVE.md` canonical): #29 Google OAuth verification ·
   #26/#28 friend beta · #282 uptime monitor · #255 off-box key escrow.

### Local gate incantations (unchanged + one new trap)

```bash
export PATH=/home/reese/.nvm/versions/node/v22.17.1/bin:$PATH && hash -r   # nvm use 22 LIES in this shell — node 26 breaks jsdom (35 phantom vitest failures)
redis-cli ping || redis-server --daemonize yes --save '' --appendonly no
# backend:  .venv/bin/python -m pytest -m "not integration" -p no:langsmith -q
# frontend: run from frontend/ (root npx = cached-Playwright ERR_INTERNAL_ASSERTION)
```

---

## WHAT WORKS NOW (verified on the merged L26 tree — do not re-investigate)

| Gate | Value |
|---|---|
| Backend unit lane | **2766 passed / 71 skipped / 0 failed** |
| Eval harness | **22/22 scenarios**, `SCENARIO_FLOOR=18`, `extract_candidates` byte-untouched (tripwire test) |
| Alembic | single head **0055**; chain 0052→0053→0054→0055; 0055 real up/down smoked on throwaway local PG16 |
| Frontend vitest (node 22) | **624 passed / 86 files** (all six structural gates included) |
| `tsc -b` · lint · build | clean · 0 errors (1 pre-existing warning `useStageStream.ts`) · clean |
| Playwright e2e (local, node 22) | **83 passed / 0 failed / 11 skipped** incl. editor-persistence CAS, a11y on flipped pages, geometry guards, new ≥1.8× media-box assertion |
| Layer 0 (on lanes) | ruff 0 · mypy 0 · bandit 0/0 (coverage/pip-audit: CI authoritative, local noise documented) |

Known pre-existing failure that is NOT ours: `tests/test_response_models.py::test_every_documented_json_route_declares_response_model`
under Track A's alternate pytest invocation — fails on pristine base too; logged in `docs/OFF_COURSE_BUGS.md`.

---

## FACTS WORTH NOT RE-DERIVING (this wave)

- **`nvm use 22` prints success but does NOT change `node` resolution in this harness's bash** —
  prepend `~/.nvm/versions/node/v22.17.1/bin` to PATH + `hash -r`. Verified: same tree 589/624 on
  node 26 → 624/624 on node 22.
- **The crop track's `x` is the clamped LEFT edge in source px** — the exact sendcmd values; render
  and frontend preview share one geometry definition. Cuts are unsmoothed value jumps; lerp between
  keyframes, snap at `cuts[]`.
- **The computed crop track lives on `clips.reframe_track_jsonb`, NOT the EditDocument** — a worker
  write into the CAS-revisioned edit doc would invalidate client autosaves. #396's manual override
  layers in the edit doc later; two layers, two homes.
- **`extract_candidates` must stay byte-identical** — the entire eval harness's green-by-construction
  argument rests on it; the hybrid merge is a separate pure layer (`clip_engine/merge.py`).
- **`applied_*` is creator-typed ONLY; the pipeline writes `suggested_*`** — publish fallback is
  `applied or suggested or (video.title | "#Shorts")`, pre-clamped, pinned by worker tests.
- **Speaker→track mapping is per-shot** (`(shot_idx, speaker)` keys) — tracks can't span camera cuts.
- **`analyze_video_context` can never raise** — catch-all → `context_skipped` SSE step → chain
  continues. If clips look signal-only, check for that event before suspecting the merge layer.
- **Track C's sizing budget excludes the trim filmstrip** — at 1440×900 it sits just below the fold
  inside the scrollable stage cell BY DESIGN (including it caps media at ~1.45× and fails the 1.8×
  gate). Don't "fix" it back under the player.
- **`AppliedTitleField` gained optional `autoEdit`/`onClose` props** (additive; default unchanged) —
  `ClipMetadataPanel`'s one-click Edit depends on them.

---

## KEY COORDINATES & FACTS

| Thing | Value |
|---|---|
| Repo | `github.com/reese8272/creatorclip` |
| Production | `https://autoclip.studio` — VM + docker-compose + Cloudflare tunnel (**not** Render) |
| Deploy chain | push to `main` → `docker-publish.yml` → `deploy.yml` (staging gate → prod) — the L26 push auto-deploys |
| Staging sync | after any merge to `main`: `git push origin origin/main:staging` (`docs/BRANCHING.md`) |
| Last landed | **Lane L26** (414–426) — three lane branches merged serially on `lane/l26`, then to `main` |
| Alembic head | **`0055_clip_reframe_track`** (0053 video_context · 0054 suggested metadata · 0055 crop track — all expand-only) |
| Active lane | **L26 tail** (422 staging verification) → then resume **L25 Batch C** (393 next; 394/396 re-scoped per DECISIONS) |
| New config keys | `VIDEO_CONTEXT_ENABLED` · `AUTO_CLIP_METADATA` · `ANTHROPIC_MODEL_VIDEO_CONTEXT/CLIP_METADATA` · `VIDEO_CONTEXT_TRANSCRIPT_MAX_CHARS` · `LLM_CANDIDATES_MAX` · `TRANSCRIPTION_DIARIZE_ENABLED` · `REFRAME_CUT_ENABLED` + 4 planner thresholds — all in `.env.example`, all with working defaults (no VM `.env` edit required) |
| Node for local gates | `/home/reese/.nvm/versions/node/v22.17.1/bin` on PATH + `hash -r` (**`nvm use` alone is a lie**) |
| Python | `.venv/bin/python` (system `python3` lacks pydantic; user-site has stale FastAPI) |
| Redis for unit lane | `redis-server --daemonize yes --save '' --appendonly no` |
| Layer 0 | `.venv/bin/python .claude/skills/production-assessment/scripts/run_layer0.py` — repo root |
| Secrets | `.env` on the VM; rotation runbook `docs/RUNBOOKS.md`. **Names only — never values.** |

---

## CONSTRAINTS & GOTCHAS (carried forward — all still true)

- Run frontend commands from `frontend/` (root npx resolves a cached Playwright → `ERR_INTERNAL_ASSERTION`).
- Chain gates with `&&`, never `;`; piped `| tail` eats non-zero exits (`set -o pipefail` or check explicitly).
- `ruff format` reflows code between Edits — re-read before editing the same region. CI runs `ruff format --check`.
- Backticks in `git commit -m` trigger command substitution — heredoc.
- Visual baselines come from `ubuntu-latest` only (WSL2 AA); local visual pass is suggestive, not proof.
- Playwright e2e: `--workers=1` on this box after heavy runs (OOM `Target crashed` flakes).
- Six structural gates in `frontend/src/test/` fail `npm test`; honesty copy is pinned — don't "tidy".
- Radix Select `__none__` sentinel stays; `min-h-0` everywhere in flex chains; `onWheel` needs `{passive:false}`.
- Postgres GUC: always `NULLIF(current_setting('app.creator_id', true), '')::uuid` (0053 uses it).
- Integration/RLS tests need Docker (absent here) — CI-verified only; say so plainly.

---

## OPEN, LOGGED, NOT FIXED

Canonical list is `docs/OFF_COURSE_BUGS.md`. Most likely to matter first:

1. **Prod migrations run with NO pre-migration safety dump** (`BACKUP_R2_BUCKET` unset, Issue 256).
   L26's 0053–0055 are expand-only so they clear the logged bar — but set the bucket before the
   first destructive migration.
2. **`clips/` storage is not creator-scoped** → `DELETE /auth/me` purge matches nothing (right-to-erasure gap).
3. Roving tabindex on the timeline · Review-queue badge counts rendered clips · `App.tsx` `*` route
   silently redirects typos · `ORIGINALITY_SIMILARITY_THRESHOLD=0.92` unvalidated · export collector
   omits ~10 tables · e2e mock's known unmodeled GETs (`/api/notifications`, `/clips/c1/download`).
4. `tests/test_response_models.py` route-model gap (peaks/stream) — pre-existing, off-course-logged.

---

## POINTERS

| Doc | Purpose |
|---|---|
| `docs/issues.md` | Work queue — **§ Lane L26 (414–426): all closed except 422's staging boxes. Then L25 Batch C** |
| `docs/PROJECT_STATE.md` | Progress log — top entries: L26 declaration + build close-outs |
| `docs/DECISIONS.md` | 2026-08-04 entries: L26 10-decision block · Track A build deviations · Deepgram surcharge · Track C deviations + merge notes |
| `docs/DEPLOYMENT.md` | § Speaker-Aware Reframe — Staging Rollout Checklist (**NEXT ACTION 1**) |
| `docs/SOT.md` | Stack, schema, structure — updated for VideoContext/crop-track/stage components |
| `docs/OFF_COURSE_BUGS.md` · `docs/GO_LIVE.md` | Logged defects · operator launch scorecard |
| `docs/BRANCHING.md` · `docs/MIGRATIONS.md` · `docs/CLIPPING_PRINCIPLES.md` | Promotion model · migration templates · principles registry |
| `~/.claude/ISSUES_LOG.md` | Cross-project incident log |
| Memory dir | `/home/reese/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` |
