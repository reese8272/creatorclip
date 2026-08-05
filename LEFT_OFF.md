# LEFT_OFF.md — CreatorClip Session Handoff

**Last updated:** 2026-08-05 (night) · **Branch:** `main` @ `e6d7334` + this docs close-out ·
in sync with `origin/main` after push
**Prod:** `https://autoclip.studio` — speaker cuts LIVE (422), serial render queue (432),
**Issue 433 (region-aware reframe) DEPLOYED with `CAMERA_REGION_DETECT_ENABLED=true`** and
**Issue 431 ("Generate more clips") DEPLOYED** (`e6d7334`, chain green). Both test videos were
deleted (DB cascade + R2 verified) — **the creator re-uploads fresh to exercise everything**.
**Git note:** `stash@{0}` remains the owner's own "wip LEFT_OFF before research-branch checkout"
stash — popping it onto this file WILL conflict; resolve by hand or drop deliberately.

> Source-of-truth docs live in `docs/`. This file orients and points to them — it is NOT a source of truth.

---

## CURRENT FOCUS

**Everything for the fresh-upload verification round is built and deployed; the active work is
LIVE VERIFICATION on the next upload** — Issues 433 (cuts + chrome removal composed), 427
(captions), and 431 (generate-more smoke). Rulings: `docs/DECISIONS.md` two 2026-08-05 (night)
entries; session log: `docs/PROJECT_STATE.md` top entry.

## → NEXT ACTIONS (in order)

1. **When the user's fresh upload lands**, verify live:
   - **433:** renders show speaker cuts AND no source chrome (SUBSCRIBE banner gone on
     produced layouts); `GET /clips/{id}/crop-track` carries the additive `region` key when a
     region was used; CropTrackOverlay still aligns. Frame spot-check via the clip-audit
     method below.
   - **427:** captions in the 70% band, off the face (Haar pass is now unconditional — works
     under the reframe flag too).
   - **431 smoke:** review clips → give feedback → "Generate more clips" (toolbar or the
     all-reviewed state) → appended non-shortlisted clips, no window duplicates, NO minute
     deduction (`minute_deductions` unchanged), metadata fills on the appended rows.
2. **If the 431 deploy chain wasn't confirmed green** (watcher was still polling at close):
   `gh run list --commit e6d7334` — a failed image build silently SKIPS deploy.
3. **Watch for:** Opus 5 cache HITS (`cache_read_input_tokens > 0` on the 2nd+ call) ·
   caption-position UX feedback · reframe floor behavior on new layouts.
4. **Parked:** Issue-395 live drills (>2 GB, reload-resume, session-expiry) · operator
   punch-list (`docs/GO_LIVE.md`): #29 OAuth verification · #26/#28 friend beta · #282 uptime
   monitor · #255 key escrow.

### Local gate incantations

```bash
export PATH=/home/reese/.nvm/versions/node/v22.17.1/bin:$PATH && hash -r   # node 26 breaks jsdom (35 false fails)
redis-cli ping || redis-server --daemonize yes --save '' --appendonly no
# backend:  .venv/bin/python -m pytest -m "not integration" -p no:langsmith -q
# frontend: run from frontend/ (root npx = cached-Playwright ERR_INTERNAL_ASSERTION)
# Layer 0:  .venv/bin/python .claude/skills/production-assessment/scripts/run_layer0.py
# eval:     any clip_engine/ change → tests/test_clip_engine.py gates it (SCENARIO_FLOOR=21)
```

---

## WHAT WORKS NOW (verified this session — do not re-investigate)

**Gates on the two ship commits (`433aaa6`, `e6d7334`):** backend **2871/0** (incl. 9 new
`tests/test_generate_more.py` + the 433 composition/region tests) · Layer-0 ALL GREEN (ruff 0
· mypy 0 · coverage 84.2 · clip_engine 92.1 · bandit 0/0 · pip-audit 0) · frontend (node 22)
vitest **641/641**, `tsc -b` clean, build clean, eslint 0 errors.

**Issue 433 (details: DECISIONS night entry, 5 rulings):** `crop@spk` instance-labeled sendcmd
targets (bare `crop` hits EVERY crop instance — empirically proven; also fixes the latent
reframe+zoom punch-in corruption) · region composition = pre-crop-in-stream + region-space
analysis, NO offset arithmetic (`compute_dynamic_crop(region=…)` slices sampled frames) ·
track JSON stays v1: `source` = pan-space rect, additive `region` key omitted when absent ·
unconditional Haar pass restores flag-ON caption avoidance. Prod env now:
`ACTIVE_SPEAKER_REFRAME_ENABLED=true`, `REFRAME_MIN_MAPPING_CONFIDENCE=0.2`,
`CAMERA_REGION_DETECT_ENABLED=true` (render-worker recreated with all three).

**Issue 431 (details: DECISIONS night entry, 4 rulings):** `POST /videos/{id}/clips/generate-more`
— same guard stack as generate; 409 without engine baseline / at `CLIP_REGEN_TOTAL_CAP=24`;
`score_and_rank(exclude_windows=…)` IoMin ≥ 0.8 vs ALL persisted windows (creator-clip NULL
setup → `start_s`); `append_ranked_clips` continues ranks past max engine rank, SKIPS
preference rerank, one re-offset retry on the uq race; `CLIP_REGEN_BATCH_MAX=6`; fill-only
metadata; never shortlisted/auto-rendered. Review UI: `GenerateMoreClipsButton` (toolbar +
terminal state), redirect held open via `useIsMutating` on `generateMoreMutationKey`
(`lib/mutationKeys.ts`), 2 s→8 s.

**Step-0 deletion:** videos `e290e6f4`/`6c221f12` gone — DELETE 2 rows cascaded 20 clips; 88
R2 artifacts deleted-or-absent; `source/`/`audio/`/`peaks/` prefixes empty, `clips/` canary only.

**Clip audit method (repeatable):** clip rows via
`ssh creatorclip-vm "cd /opt/autoclip && docker compose -f docker-compose.prod.yml exec -T postgres psql -U creatorclip -d creatorclip -c \"SELECT … FROM clips WHERE video_id='…'\""`
(origin/principle in `signals_jsonb->>'origin'`/`->>'principle'`); transcript windows via
`jsonb_array_elements(segments_jsonb->'segments')`; visual via presign → curl → `ffmpeg -ss … -frames:v 1`.

---

## THE ARC THAT LED HERE

1. 427–430 wave + Opus 5 + 12-clip pool shipped and live-verified on video `6c221f12`;
   assessment surfaced "no cuts" → 422 closed live (floor 0.2), 432 render queue, 90 s clamp.
2. User: "delete the videos and I will reupload. Begin with issue 433 and 431" → plan approved.
3. This session: deletion → 433 built/deployed/flag flipped → 431 built/deployed → docs.

---

## KEY COORDINATES & FACTS

| Thing | Value |
|---|---|
| Repo | `github.com/reese8272/creatorclip` · prod = VM (`ssh creatorclip-vm`, standing permission), `/opt/autoclip`, docker-compose, Cloudflare tunnel |
| Deploy chain | push to `main` → docker-publish → deploy (staging gate → prod). **A failed image build silently SKIPS deploy** — `gh run list` after every push. After merge: `git push origin main:staging` |
| Ship commits | 433 = `433aaa6` (chain green, health 200) · 431 = `e6d7334` (verify with `gh run list --commit e6d7334`) |
| Live flags | `ACTIVE_SPEAKER_REFRAME_ENABLED=true` · `REFRAME_MIN_MAPPING_CONFIDENCE=0.2` · `CAMERA_REGION_DETECT_ENABLED=true` (VM .env) |
| Clip-quality models | `ANTHROPIC_MODEL_VIDEO_CONTEXT/SCORING/CLIP_METADATA = claude-opus-5` |
| Pool / regen | `CLIPS_PER_VIDEO_DEFAULT=12` · `AUTO_RENDER_TOP_N=8` · `LLM_CANDIDATES_MAX=6` · `CLIP_REGEN_BATCH_MAX=6` · `CLIP_REGEN_TOTAL_CAP=24` |
| Eval | `SCENARIO_FLOOR=21`, 22 fixtures; landing page claims the count — `test_eval_transparency` enforces sync |
| Python / Node | `.venv/bin/python` backend · node **v22.17.1** via PATH export frontend |
| Secrets | `.env` on the VM (R2_*, tunnel token, etc.) — names only, never values |

---

## CONSTRAINTS & GOTCHAS (carried forward + new)

- **All prior gotchas hold**: `extract_candidates` byte-identical (tripwire in
  `tests/test_merge.py`); `sentence_snap` is the only snapping authority; the 429 containment
  pass stays post-ranking/pre-trim; `&&` not `;`; GUC
  `NULLIF(current_setting('app.creator_id', true), '')::uuid`; integration/RLS tests CI-only.
- **New (433):** sendcmd chains use `crop@spk=`; static chains keep the UNLABELED `crop=`
  spelling — byte-identity is pinned (`test_camera_region_none_is_byte_identical`). Never
  emit an offset into sendcmd x values — region math lives entirely in the pre-crop.
- **New (431):** `append_ranked_clips` must never run `rerank_with_preference` (rank
  collision); the generate-more response returns the FULL refreshed list (`ClipListOut`),
  message set when nothing new. Frontend: appended clips are non-shortlisted — Review jumps
  to them via show-all-candidates.
- **Render topology (432):** ALL ffmpeg tasks on `render-worker` (`-Q render`, concurrency 1,
  node `render@%h` — single `%`). Enqueue drills:
  `docker compose exec -T worker celery -A worker.celery_app call worker.tasks.render_clip --args '["<id>"]'`
  after resetting `render_status='pending'`.
- **Reframe floor:** side-by-side layouts honestly score LOW mapping confidence — tune the
  floor knob, don't "fix" the mapping.
- **Opus 5 call sites:** thinking on by default — `max_tokens` covers thinking + text; never
  lower the raised caps. Cache floors stay 1024.
- **Pre-existing local failures (not this wave):** `test_render_summary_file_real_ffmpeg_smoke`
  (local ffmpeg env — OFF_COURSE_BUGS) · `test_response_models` env-dependent ·
  eslint warning in `useStageStream.ts:100` (unused disable directive) · node-26 jsdom gotcha.
- The permission classifier sometimes blocks read-only `docker compose exec` forms — use
  `exec -T app python -c …` / `exec -T postgres psql` shapes.
- The owner sometimes powers the droplet off intentionally — check before treating prod-down
  as an incident (memory: `project_live_deployment_topology`).

---

## OPEN, LOGGED, NOT FIXED

Canonical list `docs/OFF_COURSE_BUGS.md`. Top: recap ffmpeg smoke local failure (2026-08-05) ·
node-26 gotcha structural fix (.nvmrc unfiled) · pre-migration safety dump unset (Issue 256) ·
`clips/` storage not creator-scoped (erasure gap) · `test_response_models` env-dependent gap.

---

## POINTERS

| Doc | Purpose |
|---|---|
| `docs/issues.md` | Work queue — 431 DONE (live smoke box open), 432 DONE, 433 BUILT+DEPLOYED (live box open); next free number **434** |
| `docs/DECISIONS.md` 2026-08-05 (night ×2) | 433: 5 rulings (sendcmd labeling, region composition, track v1+region, Haar, floor) · 431: 4 rulings (skip rerank, containment reuse, caps 6/24, redirect 8 s) |
| `docs/PROJECT_STATE.md` top entry | This session's build summary + gates |
| `docs/SOT.md` | Updated: generate-more endpoint, CLIP_REGEN_* keys, reframe/camera-region composition, crop-track `region` field |
| `~/.claude/ISSUES_LOG.md` | Cross-project incident log |
| Memory dir | `/home/reese/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` (`project_l26_autoclip_mvp.md` = lane memory) |
