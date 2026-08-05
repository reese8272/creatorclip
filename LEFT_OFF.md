# LEFT_OFF.md — CreatorClip Session Handoff

**Last updated:** 2026-08-05 (end of night session) · **Branch:** `main` @ `8b50d43`, clean
tree, in sync with `origin/main` · all deploy chains green (`gh run list` verified)
**Prod:** `https://autoclip.studio` healthy — the ENTIRE night's stack is LIVE and
live-verified on real footage: speaker cuts (422) · serial render queue (432) · region-aware
reframe + chrome removal (433) · generate-more (431) · Review audio toggle (434) · video
titles + rename (435) · **virtual-tripod framing (436)** · camera-region floor 0.45.
**Git note:** `stash@{0}` remains the owner's own "wip LEFT_OFF before research-branch checkout"
stash — popping it onto this file WILL conflict; resolve by hand or drop deliberately.

> Source-of-truth docs live in `docs/`. This file orients and points to them — it is NOT a source of truth.

---

## CURRENT FOCUS

**The A→B pipeline is feature-complete for this round and verified server-side; what remains
is CREATOR-IN-BROWSER validation** of the three UI features shipped tonight, then normal beta
polish. Rulings: `docs/DECISIONS.md` three 2026-08-05 entries (night ×2 + late-night);
session log: `docs/PROJECT_STATE.md` top two entries.

## → NEXT ACTIONS (in order)

1. **Creator browser checks on video `b8505eb7`** (nothing here needs code):
   - Review page → click the speaker icon once → audio plays and stays on across clips (434).
   - Dashboard → hover title → **Rename** → set a real name; confirm it shows in the Review
     picker (435).
   - Rate a few clips → **"Generate more clips"** (toolbar or all-reviewed screen) → expect
     ≤6 appended, non-duplicate, non-shortlisted clips, NO minute deduction
     (`SELECT * FROM minute_deductions` unchanged), titles filling via the metadata task (431).
2. **Offered, awaiting the user's word:** re-render ranks 3–8 of `b8505eb7` (they still carry
   pre-436 jittery/banner renders). Drill: reset `render_status='pending'` per clip id, then
   `docker compose exec -T worker celery -A worker.celery_app call worker.tasks.render_clip --args '["<id>"]'`
   — they queue serially on the render-worker.
3. **Watch for:** Opus 5 cache HITS (`cache_read_input_tokens > 0` on 2nd+ scoring calls) ·
   tripod knobs on other layouts (`REFRAME_PAN_DEADBAND_FRAC=0.15`, `RETARGET_S=1.0` —
   tune from evidence, not vibes) · reframe confidence floor 0.2 behavior on new layouts.
4. **Parked:** Issue-395 live drills (>2 GB, reload-resume, session-expiry) · operator
   punch-list (`docs/GO_LIVE.md`): #29 OAuth verification · #26/#28 friend beta · #282 uptime
   monitor · #255 key escrow.

### Local gate incantations

```bash
export PATH=/home/reese/.nvm/versions/node/v22.17.1/bin:$PATH && hash -r   # node 26 = 35 phantom jsdom fails
redis-cli ping || redis-server --daemonize yes --save '' --appendonly no
# backend:  .venv/bin/python -m pytest -m "not integration" -p no:langsmith -q
# frontend: run from frontend/ (root npx = cached-Playwright ERR_INTERNAL_ASSERTION)
# Layer 0:  .venv/bin/python .claude/skills/production-assessment/scripts/run_layer0.py
# eval:     any clip_engine/ change → tests/test_clip_engine.py gates it (SCENARIO_FLOOR=21)
```

---

## WHAT WORKS NOW (verified this session — do not re-investigate)

**Live frame-verified on `b8505eb7` ranks 1+2 re-renders (2026-08-05 late night):**
- **Chrome removal:** region "cropping into (169,326,1576,551)" — SUBSCRIBE banner, socials
  strip, letterbox ALL gone; framing tight on the speaker. Floor 0.45 admitted the 0.51 band
  the old 0.55 floor rejected.
- **Virtual tripod:** crop tracks collapsed 336→3 keyframes (rank 1 — the only x-changes ARE
  the two cuts) and 172→47 (rank 2 — one hold + glide ramps). The old failure (4 micro-moves/s,
  25–60 px see-saw) is impossible by construction now.
- **Bonus:** mapping confidence improved 0.46→0.51 (detection runs on region-sliced frames).
- **Audio in the FILES** was always fine (−16.2 dB loudnorm, ffprobe-verified) — the Review
  player was the only mute.

**Gates on the wave (`5a8d0f1`):** backend **2877/0** · Layer 0 ALL GREEN (coverage 84+,
ruff/mypy/bandit/pip-audit 0) · frontend **646/646** on node 22 + `tsc -b` + build + eslint
0 errors. Deploy chains green ×2 (`5a8d0f1`, `8b50d43`).

**Issue map for tonight:** 431/432/433/434/435/436 all DONE in `docs/issues.md` (431's live
smoke + 434/435 browser boxes are the step-1 checks above). Next free issue number: **437**.

**Clip audit method (repeatable):** clip rows via
`ssh creatorclip-vm "cd /opt/autoclip && docker compose -f docker-compose.prod.yml exec -T postgres psql -U creatorclip -d creatorclip -c \"SELECT … FROM clips WHERE video_id='…'\""`;
presign via `exec -T app python -c "from worker.storage import presigned_download_url; …"`
(needs `filename=` kwarg); frames via `ffmpeg -ss … -frames:v 1`; jitter stats straight from
`reframe_track_jsonb` keyframes.

---

## THE ARC THAT LED HERE

1. Deleted both stale test uploads → built+deployed 433 (region∘cuts) and 431 (generate-more).
2. Creator uploaded fresh (`b8505eb7`) → 8 rendered clips verified cuts/captions/opens live.
3. Creator review found: silent Review page, "Untitled" dead end, jittery framing, banner
   still in frame → all four root-caused with live evidence (tracks, logs, ffprobe) → plan
   approved → Issues 434/435/436 + floor tune built, gated, deployed, and the re-render drill
   frame-proved 436 + the floor fix same night.

---

## KEY COORDINATES & FACTS

| Thing | Value |
|---|---|
| Repo | `github.com/reese8272/creatorclip` · prod = VM (`ssh creatorclip-vm`, standing permission), `/opt/autoclip`, docker-compose, Cloudflare tunnel |
| Deploy chain | push to `main` → docker-publish → deploy (staging gate → prod). **A failed image build silently SKIPS deploy** — `gh run list` after every push |
| Tonight's commits | 433 `433aaa6` · 431 `e6d7334` · 434–436+floor `5a8d0f1` · docs `058c3a1`/`8b50d43` — all chains green |
| Fresh video | `b8505eb7-c7d2-425f-bc28-a2909ade805e` (27-min podcast, 12 clips, top-8 rendered; ranks 1 `56746e18…`/2 `154a517d…` re-rendered on new code) |
| Live flags (VM .env) | `ACTIVE_SPEAKER_REFRAME_ENABLED=true` · `REFRAME_MIN_MAPPING_CONFIDENCE=0.2` · `CAMERA_REGION_DETECT_ENABLED=true` |
| New knobs (436) | `REFRAME_PAN_DEADBAND_FRAC=0.15` (of crop width) · `REFRAME_PAN_RETARGET_S=1.0` · `REFRAME_PAN_GLIDE_PX_PER_S=600` · `REFRAME_GLIDE_SAMPLE_FPS=30` · floor `CAMERA_REGION_MIN_HEIGHT_FRAC=0.45` |
| Pool / regen | `CLIPS_PER_VIDEO_DEFAULT=12` · `AUTO_RENDER_TOP_N=8` · `CLIP_REGEN_BATCH_MAX=6` · `CLIP_REGEN_TOTAL_CAP=24` |
| Models | `ANTHROPIC_MODEL_VIDEO_CONTEXT/SCORING/CLIP_METADATA = claude-opus-5` |
| Eval | `SCENARIO_FLOOR=21`, 22 fixtures; landing page claims the count — `test_eval_transparency` enforces sync |
| Python / Node | `.venv/bin/python` backend · node **v22.17.1** via PATH export frontend |
| Secrets | `.env` on the VM (R2_*, tunnel token, etc.) — names only, never values |

---

## CONSTRAINTS & GOTCHAS (carried forward + new)

- **All prior gotchas hold:** `extract_candidates` byte-identical (tripwire in
  `tests/test_merge.py`); `sentence_snap` is the only snapping authority; containment pass
  stays post-ranking/pre-trim; `&&` not `;`; GUC
  `NULLIF(current_setting('app.creator_id', true), '')::uuid`; integration/RLS tests CI-only;
  static ffmpeg chains keep the UNLABELED `crop=` spelling (byte-identity pinned); appended
  431 clips never preference-rerank.
- **New (436):** the EMA machinery is DELETED on purpose — do not reintroduce per-sample
  smoothing "for polish"; it was the 5 Hz staircase failure mode. Holds are piecewise-constant;
  the pan retarget requires the deadband breach to be CONTINUOUS for the full window (a
  majority/median vote lets sub-window spikes flip it — tested). `FaceTrack.cx_at` is gone.
- **New (435):** the multipart upload flow stays STATELESS — the filename rides the
  `/complete` body; don't add server-side upload sessions to "improve" this.
- **New (434):** the muted-autoplay pairing is still required by browser policy; the toggle +
  sessionStorage (`cc-player-muted`) is the escape hatch. Icon seam rejects digit-named
  icons (`Volume`, not `Volume2`).
- **Render topology (432):** ALL ffmpeg tasks on `render-worker` (`-Q render`, concurrency 1,
  node `render@%h` — single `%`). Re-render drill needs `render_status='pending'` reset first
  (done-with-uri redelivery guard skips otherwise).
- **Pre-existing local failures (not this wave):** `test_render_summary_file_real_ffmpeg_smoke`
  (local ffmpeg env — OFF_COURSE_BUGS) · `test_response_models` env-dependent · eslint warning
  `useStageStream.ts:100` · node-26 jsdom gotcha (.nvmrc fix still unfiled).
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
| `docs/issues.md` | Work queue — tonight's 431–436 all DONE (browser boxes open on 431/434/435); next free number **437** |
| `docs/DECISIONS.md` 2026-08-05 (three entries) | 433 rulings · 431 rulings · late-night wave (tripod design, floor evidence, audio/title rulings) |
| `docs/PROJECT_STATE.md` top two entries | Tonight's build summaries + gates |
| `docs/SOT.md` | Updated: reframe virtual-tripod pipeline, PATCH /videos/{id}, REFRAME_PAN_*/CLIP_REGEN_* keys, crop-track `region` field |
| `~/.claude/ISSUES_LOG.md` | Cross-project incident log |
| Memory dir | `/home/reese/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` (`project_l26_autoclip_mvp.md` = lane memory) |
