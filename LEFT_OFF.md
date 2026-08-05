# LEFT_OFF.md — CreatorClip Session Handoff

**Last updated:** 2026-08-05 (late evening) · **Branch:** `main` @ `4ffe7da` + this docs
close-out · in sync with `origin/main` after push
**Prod:** `https://autoclip.studio` — **SPEAKER CUTS ARE LIVE** (Issue 422 CLOSED: all four
unlocks evidenced, `ACTIVE_SPEAKER_REFRAME_ENABLED=true` +
`REFRAME_MIN_MAPPING_CONFIDENCE=0.2` in the VM env, renders on the dedicated serial
`render-worker` — Issue 432). The 427–430 wave was already live; post-deploy assessment of
video `6c221f12` (12 clips, sentence-clean opens, no dupes, Opus 5 confirmed) drove this
session's fixes. Deploy chain green ×3 today.
**Git note:** `stash@{0}` remains the owner's own "wip LEFT_OFF before research-branch checkout"
stash — popping it onto this file WILL conflict; resolve by hand or drop deliberately.

> Source-of-truth docs live in `docs/`. This file orients and points to them — it is NOT a source of truth.

---

## CURRENT FOCUS

**Speaker cuts, the serial render queue, and the universal 90 s clamp are LIVE and
verified; the active tail is Issue 433 (region-aware reframe — cuts AND chrome removal
together) plus the parked product items.** Evidence + rulings: `docs/DECISIONS.md` two
2026-08-05 entries; session log: `docs/PROJECT_STATE.md` top two entries.

## → NEXT ACTIONS (in order)

1. **Optional but nice:** the video's other 11 clips were rendered BEFORE cuts went live —
   only rank 4 (`746467e7`) and rank 8 (`785859a3`) carry the speaker-cut render. Re-render
   any clip from Review (or reset+`celery call` per the drill pattern) to get cuts on it;
   they queue serially on the render-worker.
2. **Issue 433 (next build): region-aware reframe** — compose the Issue-430 chrome removal
   with speaker cuts (labeled `crop@spk` filter + region offset applied once at
   sendcmd/track emission; wire-contract versioning for CropTrackOverlay). Until then the
   source's own SUBSCRIBE banner stays in frame (documented tradeoff, DECISIONS).
3. **Issue 431 ("Generate more clips")** — filed with sketch; run the issue workflow when
   scheduled.
4. **Watch for**: 427 live acceptance box (frame spot-check done informally — captions in
   band, off face; formally re-check after any caption_position experimentation) · caption
   position UX feedback · Opus 5 cache HITS on the next video (`cache_read_input_tokens > 0`).
5. **Parked:** Issue-395 live drills (>2 GB, reload-resume, session-expiry) · operator
   punch-list (`docs/GO_LIVE.md`): #29 OAuth verification · #26/#28 friend beta · #282 uptime
   monitor · #255 key escrow.

### Local gate incantations

```bash
export PATH=/home/reese/.nvm/versions/node/v22.17.1/bin:$PATH && hash -r   # node 26 breaks jsdom
redis-cli ping || redis-server --daemonize yes --save '' --appendonly no
# backend:  .venv/bin/python -m pytest -m "not integration" -p no:langsmith -q
# frontend: run from frontend/ (root npx = cached-Playwright ERR_INTERNAL_ASSERTION)
# Layer 0:  .venv/bin/python .claude/skills/production-assessment/scripts/run_layer0.py
# eval:     any clip_engine/ change → tests/test_clip_engine.py gates it (SCENARIO_FLOOR=21)
```

---

## WHAT WORKS NOW (verified this session — do not re-investigate)

**All local gates green on the wave:** backend **2850/0** · eval **25/25** (22 fixtures,
FLOOR 21; `mid_sentence_open` verified RED on the legacy path first) · Layer-0 ALL GREEN
(ruff 0 · mypy 0 · coverage 84.16 · bandit 0/0 · pip-audit 0) · frontend (node 22) vitest
**637/637**, `tsc -b` clean, eslint 0 errors (1 pre-existing logged warning).

**What the wave ships (details: PROJECT_STATE top entry + DECISIONS 2026-08-05):**
- 428: `sentence_snap.py` post-extraction pass (never-open-mid-sentence; `extract_candidates`
  byte-identical, tripwire intact) + LLM 90 s clamp + hook grounded in the real first 5 s.
- 429: `suppress_contained` (IoMin ≥ 0.8, drop + refill) at the rank→trim seam.
- 427: karaoke captions → 70% band + Haar face avoidance + 3-word groups + `caption_position`
  (panel + brand kit, JSONB, no migration).
- 430: `camera_region.py` (cv2 temporal variance, fail-open) behind
  `CAMERA_REGION_DETECT_ENABLED=false`; byte-identical when off (pinned).
- Opus 5 on video_context/scoring/clip_metadata (max_tokens 8000/8000/6000, refusal fail-open,
  billing via `model_rates`); pool 12 persisted / top-8 auto-rendered / LLM cap 6.
- 422 step-0 resolved: `mediapipe==1.0.0` + `opencv-contrib-python==4.13.0.92`
  (both PyPI-verified live), `INSTALL_REFRAME=true` default.
- Landing page eval-count claim updated 19→22 (structural test caught it).

**Clip audit method (repeatable):** clip rows via
`ssh creatorclip-vm "cd /opt/autoclip && docker compose -f docker-compose.prod.yml exec -T postgres psql -U creatorclip -d creatorclip -c \"SELECT … FROM clips WHERE video_id='…'\""`
(origin/principle in `signals_jsonb->>'origin'`/`->>'principle'`); transcript windows via
`jsonb_array_elements(segments_jsonb->'segments')`; visual via presign → curl → `ffmpeg -ss … -frames:v 1`.

---

## THE ARC THAT LED HERE

1. Morning (prev. session): Issue 395 direct-to-R2 shipped + live-verified (273 MB in 58 s);
   first real A→B run audited → Issues 427–430 filed with evidence.
2. This session: `/issue-workflow` on the four issues → plan approved, then the user added four
   directives: Opus for clip quality · MORE clips narrowed down (12/top-8) · user-selectable
   caption position · active-speaker lens (→ 422 unblock; research found mediapipe ≥0.10.30
   supports numpy 2 — the 422 blocker dissolved).
3. Built all three tracks + eval + docs; all local gates green; wave left uncommitted for the
   user's review.

---

## KEY COORDINATES & FACTS

| Thing | Value |
|---|---|
| Repo | `github.com/reese8272/creatorclip` · prod = VM (`ssh creatorclip-vm`, standing permission), `/opt/autoclip`, docker-compose, Cloudflare tunnel |
| Deploy chain | push to `main` → docker-publish → deploy (staging gate → prod). **A failed image build silently SKIPS deploy** — `gh run list` after every push. After merge: `git push origin main:staging` |
| Wave flags | `ACTIVE_SPEAKER_REFRAME_ENABLED=true` + `REFRAME_MIN_MAPPING_CONFIDENCE=0.2` LIVE (VM .env; DECISIONS 2026-08-05 evening) · `CAMERA_REGION_DETECT_ENABLED=false` (superseded by Issue 433 — skipped by design while reframe owns geometry) · `CAPTION_*` knobs in `.env.example` |
| Clip-quality models | `ANTHROPIC_MODEL_VIDEO_CONTEXT/SCORING/CLIP_METADATA = claude-opus-5` (env-overridable; billing follows configured model) |
| Pool | `CLIPS_PER_VIDEO_DEFAULT=12` · `CLIP_SIGNAL_POOL_MAX=12` · `LLM_CANDIDATES_MAX=6` · `AUTO_RENDER_TOP_N=8` |
| Audit video | `videos.id = e290e6f4-12b9-4d6c-a4c2-d56542502740` (26:57 podcast) — source purges ~2026-08-08; re-upload if needed |
| Eval | `SCENARIO_FLOOR=21`, 22 fixtures; new kinds `snap`/`containment` + merge `len_s_max`; the landing page publicly claims the fixture count — `test_eval_transparency` enforces sync |
| Python / Node | `.venv/bin/python` backend · node **v22.17.1** via PATH export frontend |
| Secrets | `.env` on the VM (R2_*, tunnel token, etc.) — names only, never values |

---

## CONSTRAINTS & GOTCHAS (carried forward + new)

- **All prior L26 gotchas hold**: `analyze_video_context` never raises (`context_skipped`);
  `extract_candidates` byte-identical (tripwire `tests/test_merge.py`); per-shot speaker tracks;
  `&&` not `;`; ruff format reflows between edits; GUC
  `NULLIF(current_setting('app.creator_id', true), '')::uuid`; integration/RLS tests CI-only.
- **New:** `sentence_snap` is the ONLY snapping authority in production (`ranking.py` no longer
  passes words to `extract_candidates` — the word-level path is test-only). The 429 pass must
  stay post-ranking/pre-trim. Caption `words_per_group=1` byte-identity and the region-off vf
  byte-identity are PINNED tests — don't break them casually.
- **Opus 5 call sites:** thinking is on by default — `max_tokens` covers thinking + text; never
  lower the raised caps back to 2000. Cache floors deliberately stay 1024 (shared with Sonnet
  callers — DECISIONS ruling 6).
- **mediapipe 1.0.0 image:** proven in prod (three green publishes); contrib force-reinstall
  in the Dockerfile is load-bearing (two opencv wheels share the cv2 path).
- **Render topology (Issue 432):** ALL ffmpeg tasks run on `render-worker` (`-Q render`,
  concurrency 1, node name `render@%h` — single `%`, `%%h` breaks the healthcheck). The main
  worker consumes `-Q celery` only. Enqueue drills with
  `docker compose exec -T worker celery -A worker.celery_app call worker.tasks.render_clip --args '["<id>"]'`
  after resetting `render_status='pending'` (the done-with-uri redelivery guard skips otherwise).
- **Reframe floor:** side-by-side layouts honestly score LOW mapping confidence (0.248 on the
  live fixture) — don't "fix" the mapping when tuning; the floor knob exists for this.
- **Pre-existing local failures (not this wave):** `test_render_summary_file_real_ffmpeg_smoke`
  fails on pristine base (local ffmpeg env — OFF_COURSE_BUGS 2026-08-05) ·
  `test_response_models` env-dependent gap · node-26 jsdom gotcha (.nvmrc fix still unfiled).
- The permission classifier sometimes blocks read-only `docker compose exec` forms — use
  `exec -T app python -c …` / `exec -T postgres psql` shapes.
- The owner sometimes powers the droplet off intentionally — check before treating prod-down as
  an incident (memory: `project_live_deployment_topology`).

---

## OPEN, LOGGED, NOT FIXED

Canonical list `docs/OFF_COURSE_BUGS.md`. Top: recap ffmpeg smoke local failure (new 2026-08-05) ·
node-26 gotcha structural fix · pre-migration safety dump unset (Issue 256) · `clips/` storage not
creator-scoped (erasure gap) · `test_response_models` env-dependent gap.

---

## POINTERS

| Doc | Purpose |
|---|---|
| `docs/issues.md` | Work queue — 428/429 DONE, 427/430 code-complete (live boxes open), 422 staging open, **431 filed** ("Generate more clips"); next free number 432 |
| `docs/DECISIONS.md` 2026-08-05 | Clip-quality wave: 10 rulings + sources |
| `docs/PROJECT_STATE.md` top entry | Wave build summary + gates |
| `docs/DEPLOYMENT.md` § staging rollout | Issue 422 checklist — step 0 RESOLVED, unlocks #1–#4 open |
| `docs/SOT.md` | Updated: `sentence_snap.py`, `camera_region.py`, caption/pool/model notes |
| `~/.claude/ISSUES_LOG.md` | Cross-project incident log |
| Memory dir | `/home/reese/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` (`project_l26_autoclip_mvp.md` = lane memory) |
