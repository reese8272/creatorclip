# LEFT_OFF.md — CreatorClip Session Handoff

**Last updated:** 2026-08-05 (evening) · **Branch:** `main` @ `2797385` (wave committed + pushed) ·
working tree clean after this close-out · in sync with `origin/main`
**Prod:** `https://autoclip.studio` — **the 427–430 wave IS DEPLOYED AND LIVE** (docker-publish
31034922676 green in 7m42s incl. the new mediapipe layer; deploy 31035542194 green; `/health` 200;
worker imports `mediapipe 1.0.0` + `cv2 4.13.0`; live config verified: pool 12 / signal 12 /
LLM 6 / top-8 render / Opus 5 / camera-region flag off). The VM `.env` had `CLIPS_PER_VIDEO_DEFAULT=8`
pinned — updated to 12 and app+worker recreated.
**Git note:** `stash@{0}` remains the owner's own "wip LEFT_OFF before research-branch checkout"
stash — popping it onto this file WILL conflict; resolve by hand or drop deliberately.

> Source-of-truth docs live in `docs/`. This file orients and points to them — it is NOT a source of truth.

---

## CURRENT FOCUS

**The Shorts clip-quality wave (Issues 427–430 + Opus 5 + 12-clip pool + caption position +
Issue 422 unblock) is DEPLOYED TO PROD — what remains is live verification on a real video.**
Ten rulings in `docs/DECISIONS.md` (2026-08-05 clip-quality wave entry); session log in
`docs/PROJECT_STATE.md` top entry.

## → NEXT ACTIONS (in order)

1. **Live-verify on a real upload** (audit video `e290e6f4` purges ~2026-08-08;
   re-upload the 273 MB podcast if gone) and verify: **12 persisted clips / top 8 rendered**;
   every `setup_start_s` on a sentence start (psql audit method below); no contained duplicate
   pair; no clip > 90 s; hooks match the first ~5 s of speech; Opus 5 token logs + cache hits
   (`cache_read_input_tokens > 0` on the second video).
2. **Issue 427 spot-check:** re-render one clip → presign → frame-extract → captions sit in the
   lower band off the face; try `caption_position` top/middle/bottom from the CaptionStylePanel
   and the brand kit. Then check the last 427 acceptance box in `docs/issues.md`.
3. **Issue 430 staging flip:** on the VM set `CAMERA_REGION_DETECT_ENABLED=true` (staging
   compose), render the produced-layout podcast source, frame-check that no third-party chrome
   is truncated → flip prod → check the last 430 box.
4. **Issue 422 staging checklist** (`docs/DEPLOYMENT.md` § staging rollout): step 0 is RESOLVED
   (mediapipe 1.0.0); unlock #1 half-verified live 2026-08-05 (`import mediapipe` → 1.0.0 in
   the prod worker; still record `_create_face_detector() is not None`). Unlocks #2–#4 need
   the user's 2-speaker fixture on staging; flags stay off in prod until sign-off.
5. **Parked:** Issue 431 ("Generate more clips" — filed, not built) · Issue-395 live drills
   (>2 GB, reload-resume, session-expiry) · operator punch-list (`docs/GO_LIVE.md`): #29 OAuth
   verification · #26/#28 friend beta · #282 uptime monitor · #255 key escrow.

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
| Wave flags | `CAMERA_REGION_DETECT_ENABLED=false` (flip after staging frame check) · `ACTIVE_SPEAKER_REFRAME_ENABLED=false` (422 unlocks pending) · `CAPTION_*` + `CAMERA_REGION_*` knobs in `.env.example` |
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
- **mediapipe 1.0.0 image:** first `docker-publish` after commit is the real test; contrib
  force-reinstall in the Dockerfile is load-bearing (two opencv wheels share the cv2 path).
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
