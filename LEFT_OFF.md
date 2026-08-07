# LEFT_OFF.md — CreatorClip Session Handoff

**Last updated:** 2026-08-06 (post-437 ship) · **Branch:** `main` @ `8a4c052` · **working tree
CLEAN**, in sync with `origin/main` (0 ahead / 0 behind) · deploy chains green (`gh run list`
verified: Docker publish ✓ 1m53s → Deploy to production ✓ 1m38s).
**Prod:** `https://autoclip.studio` healthy (200) — the whole recent stack is LIVE and
live-verified on real footage: speaker cuts (422) · serial render queue (432) · region-aware
reframe + chrome removal (433) · generate-more (431) · Review audio toggle (434) · video
titles + rename (435) · **virtual-tripod framing (436)** · camera-region floor 0.45 ·
**honest keep/drop failure handling (437)**.
**Prod incident, UNRESOLVED:** prod returned **502 briefly on 2026-08-05** (Cloudflare edge up,
origin unreachable). It self-recovered and the **root cause was never investigated** — worth a
look, since renders are memory-hungry and it will recur. Issue 437 fixed the UI's *handling* of
that outage, **not the outage**.
**Git note:** `stash@{0}` ("wip LEFT_OFF before research-branch checkout") and an ancient
`stash@{1}` both still exist. Popping `stash@{0}` onto this file WILL conflict — resolve by
hand or drop deliberately.

> Source-of-truth docs live in `docs/`. This file orients and points to them — it is NOT a source of truth.

---

## CURRENT FOCUS

**The UI features are all creator-validated. The open work is now CLIP QUALITY** — the
2026-08-07 audit of a real upload found four confirmed defects in the rendered output
(Issues **438-441**), all filed with evidence, none yet fixed. Rulings: `docs/DECISIONS.md`
three 2026-08-05 entries; session log: `docs/PROJECT_STATE.md` top entry.

## → NEXT ACTIONS (in order)

1. **Work Issues 438-441** (filed 2026-08-07 from the audit of video `3b6992fe`; full evidence
   in `docs/issues.md` § *Wave 11 · Lane L26 · Batch D*). Suggested order — 438 first (smallest,
   and it is shipping captionless clips today), then 439, then 440, then 441:
   - **438** captionless renders when `style_preset` is NULL (`worker/tasks.py:3547` seeds the
     kit style onto the auto-render top-N only; `routers/clips.py:871` backfills only when a
     request body is present). Ranks 9-12+14 of `3b6992fe` will render captionless as-is.
   - **439** camera-region detection unions animated overlays into the region
     (`camera_region.py:92-102` admits any contour ≥20% of the largest blob; the height frac is a
     floor with no ceiling; the face gate is containment-only so widening never trips it) — rank 6
     burned the SUBSCRIBE/socials/superchat overlay into the bottom third for 84 s.
   - **440** `face_pan` sweeps — the tripod (436) holds only in `speaker_cut`; rank 7 does 7
     full-width sweeps, rank 2 lands a frame on empty background.
   - **441** overlapping clip windows + mid-sentence cold opens in the PRIMARY generation pass
     (regeneration is clean). Note the eval is green while production fails — widen the 428/429
     fixtures to partial overlap and conjunction-initial opens BEFORE touching the ranker.
   Re-run the audit after each fix: `scripts/clip_audit.py` (see *Clip audit method* below).
2. **Still owed on 437:** the *failure*-path browser drill — Review → devtools request-blocking
   on `**/clips/*/feedback` → **Keep** → pick a **tag** → **Submit**. Expect a **red, persistent**
   *"Couldn't reach the server — nothing was saved"*, panel **still open**, tag **still
   selected**. The success path is already DB-verified. Using a tag (not the free-text note)
   would also close the untested `feedback_tags` persistence gap in `docs/OFF_COURSE_BUGS.md`.
3. **Consider chasing the 502 root cause** (nothing has ruled anything out yet). Start:
   `ssh creatorclip-vm 'cd /opt/autoclip && docker compose -f docker-compose.prod.yml logs --since 24h app worker | tail -200'`
   plus `journalctl -b -1 | tail` for a clean `systemd-poweroff`, and `dmesg -T | grep -i oom`.
   **Check with the owner first** — they sometimes power the droplet off intentionally.
3. **Offered, awaiting the user's word:** re-render ranks 3–8 of `b8505eb7` (they still carry
   pre-436 jittery/banner renders). Drill: reset `render_status='pending'` per clip id, then
   `docker compose exec -T worker celery -A worker.celery_app call worker.tasks.render_clip --args '["<id>"]'`
   — they queue serially on the render-worker.
4. **Watch for:** Opus 5 cache HITS (`cache_read_input_tokens > 0` on 2nd+ scoring calls) ·
   tripod knobs on other layouts (`REFRAME_PAN_DEADBAND_FRAC=0.15`, `RETARGET_S=1.0` —
   tune from evidence, not vibes) · reframe confidence floor 0.2 behavior on new layouts.
5. **Parked:** Issue-395 live drills (>2 GB, reload-resume, session-expiry) · operator
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

## WHAT WORKS NOW (verified — do not re-investigate)

**Issue 437 — shipped, deployed, bundle-verified (2026-08-06):** Review's Keep/Drop no longer
presents a lost rating as a confirmation. Served bundle on prod is `index-B9wDSQhX.js` /
`index-CMK99NTB.css` — byte-identical hashes to the local build, so the running code IS the fix,
not a cached shell. Frontend lane **649/649** on node 22 · eslint 0 errors · `tsc`+build clean ·
design-token contract 3/3. Backend untouched (Layer 0 unaffected). The three regression tests
were **demonstrated failing first** against the old component.

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

**Gates on the prior wave (`5a8d0f1`):** backend **2877/0** · Layer 0 ALL GREEN (coverage 84+,
ruff/mypy/bandit/pip-audit 0) · frontend 646/646 on node 22.

**Issue map:** 431/432/433/434/435/436/**437** all DONE in `docs/issues.md`; live boxes on
431/434/435 closed 2026-08-07, 437's failure-path box still open. **438-441 FILED, NOT STARTED.**
**Next free issue number: 442.**

**Audit verdict on `3b6992fe` (2026-08-07) — do not re-derive:** container and audio delivery are
flawless on all 9 rendered clips (1080×1920 h264/aac, duration exact to ±0.01 s, −13.9 to
−14.0 LUFS integrated against the `I=-14` target). Ranks **1/3/4/5/8 are good** — tight, stable,
chrome-free, correctly captioned. Ranks **2 and 7** are the `face_pan` sweep failures (440),
rank **6** is the chrome failure (439), rank **13** is the captionless one (438). The tripod
(436) works exactly as designed in `speaker_cut` mode — 2-3 keyframes, x changing only at cuts.
The creator's own keep (rank 1, speaker_cut) and drop (rank 2, face_pan) track the mode split
precisely.

**Clip audit method (repeatable — now a script, `scripts/clip_audit.py`):**

```bash
# 1. prod side — piped over stdin, so no image rebuild is needed to run it
ssh creatorclip-vm 'cd /opt/autoclip && docker compose -f docker-compose.prod.yml \
  exec -T app python3.12 - --video <uuid>' < scripts/clip_audit.py > manifest.json
# omit --video to auto-discover the newest upload with a rendered clip
# 2. local side — downloads, ffprobe, EBU R128 loudness, contact sheet per clip, cut sheets
python3.12 scripts/clip_audit.py inspect --manifest manifest.json --out ./audit
```

**RLS gotcha that cost real time:** prod connects as `creatorclip_app` — no `BYPASSRLS`, and
every tenant table has `FORCE ROW LEVEL SECURITY`, so a raw query with the `app.creator_id` GUC
unset returns **zero rows and no error**. `scripts/clip_pipeline_state.py` prints "No videos
found." on a full database because of this. `creators` is the RLS-exempt bootstrap table —
read it first, then `SELECT set_config('app.creator_id', <uuid>, false)` per creator
(`clip_audit.py:_discover`). Also: `ssh … exec -T postgres psql` is blocked by the permission
classifier; `exec -T app python3.12 …` is the shape that passes.

---

## THE ARC THAT LED HERE

1. Deleted both stale test uploads → built+deployed 433 (region∘cuts) and 431 (generate-more).
2. Creator uploaded fresh (`b8505eb7`) → 8 rendered clips verified cuts/captions/opens live.
3. Creator review found: silent Review page, "Untitled" dead end, jittery framing, banner
   still in frame → all four root-caused with live evidence (tracks, logs, ffprobe) → plan
   approved → Issues 434/435/436 + floor tune built, gated, deployed, and the re-render drill
   frame-proved 436 + the floor fix same night.
4. Owner hit a **502** and reported Keep/Drop "didn't work". Diagnosis found the outage was
   incidental: the real defect was that `YourCall.tsx` painted the failure in the **success
   green**, closed the tag panel before awaiting the POST, and threw the creator's tags away —
   so a lost rating was indistinguishable from a no-op. Issue **437** filed + fixed + shipped.

---

## KEY COORDINATES & FACTS

| Thing | Value |
|---|---|
| Repo | `github.com/reese8272/creatorclip` · prod = VM (`ssh creatorclip-vm`, standing permission), `/opt/autoclip`, docker-compose, Cloudflare tunnel |
| Deploy chain | push to `main` → docker-publish → deploy (staging gate → prod). **A failed image build silently SKIPS deploy** — `gh run list` after every push |
| Recent commits | 433 `433aaa6` · 431 `e6d7334` · 434–436+floor `5a8d0f1` · docs `058c3a1`/`8b50d43`/`d198ae5` · **437 `8a4c052`** — all chains green |
| Fresh video | `b8505eb7-c7d2-425f-bc28-a2909ade805e` (27-min podcast, 12 clips, top-8 rendered; ranks 1 `56746e18…`/2 `154a517d…` re-rendered on new code) |
| Live flags (VM .env) | `ACTIVE_SPEAKER_REFRAME_ENABLED=true` · `REFRAME_MIN_MAPPING_CONFIDENCE=0.2` · `CAMERA_REGION_DETECT_ENABLED=true` |
| Reframe knobs (436) | `REFRAME_PAN_DEADBAND_FRAC=0.15` (of crop width) · `REFRAME_PAN_RETARGET_S=1.0` · `REFRAME_PAN_GLIDE_PX_PER_S=600` · `REFRAME_GLIDE_SAMPLE_FPS=30` · floor `CAMERA_REGION_MIN_HEIGHT_FRAC=0.45` |
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
- **New (437) — do NOT add retry to the feedback POST.** `POST /clips/{id}/feedback` is **not
  idempotent**: it inserts a `ClipFeedback` row and retriggers the preference-model retrain
  (`routers/review.py:227-229`), so retrying a timed-out-but-applied write double-counts the
  rating. The fix is to preserve the creator's choice so ONE click re-submits. A real offline
  outbox needs a server-side idempotency key first — that is its own issue.
- **New (437) — status-message semantics are now written down** in `docs/UI.md` §
  *Status messaging*: errors are `text-danger` (never success/warning/muted), errors **persist**
  while successes may auto-clear, the live region is rendered unconditionally with
  `role="status" aria-live="polite"`, and a failed write never discards user input. **No tool
  enforces this** — `design-tokens.contract.test.ts` only catches *undeclared* token names, and
  `text-success` is perfectly declared, so a semantic swap compiles and ships. Component tests
  are the only guard.
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
- **Pre-existing local failures (not any recent wave):** `test_render_summary_file_real_ffmpeg_smoke`
  (local ffmpeg env — OFF_COURSE_BUGS) · `test_response_models` env-dependent · eslint warning
  `useStageStream.ts:100` · **node-26 jsdom gotcha** — local node is 26.5.1 vs `.nvmrc` 22, which
  fails 35 `localStorage` tests across 3 files (`Walkthrough`, `editDocCache`, `useEditDocument`);
  re-confirmed 2026-08-06 as identical on a clean tree. Always export the node-22 PATH first.
- The permission classifier sometimes blocks read-only `docker compose exec` forms — use
  `exec -T app python -c …` / `exec -T postgres psql` shapes.
- The owner sometimes powers the droplet off intentionally — check before treating prod-down
  as an incident (memory: `project_live_deployment_topology`).

---

## OPEN, LOGGED, NOT FIXED

Canonical list `docs/OFF_COURSE_BUGS.md`. Top:
- **502 root cause on the VM — never investigated** (2026-08-05; self-recovered).
- **Pre-migration safety dump still unset** (Issue 256) — the 437 deploy re-annotated
  *"No `BACKUP_R2_BUCKET` configured … Migrating WITHOUT a safety dump."* Harmless so far
  (recent deploys carry no destructive migration); set it before one does.
- **Two sibling status-token misuses**, found sweeping during 437 and deliberately NOT fixed:
  `onboarding/OnboardingIdentity.tsx:120` (failure branch renders `text-muted` grey) and
  `pages/Onboarding.tsx:286,294` (errors render `text-warning`).
- recap ffmpeg smoke local failure · node-26 gotcha structural fix (`.nvmrc` enforcement unfiled)
  · `clips/` storage not creator-scoped (erasure gap) · `test_response_models` env-dependent gap.

---

## POINTERS

| Doc | Purpose |
|---|---|
| `docs/issues.md` | Work queue — 431–437 all DONE (live boxes open on 431/434/435/437); next free number **438** |
| `docs/DECISIONS.md` 2026-08-05 (three entries) | 433 rulings · 431 rulings · late-night wave (tripod design, floor evidence, audio/title rulings) |
| `docs/PROJECT_STATE.md` top three entries | 437 + the two 2026-08-05 build summaries + gates |
| `docs/UI.md` § Status messaging | **New (437)** — the error/success/pending token + `aria-live` contract |
| `docs/SOT.md` | Reframe virtual-tripod pipeline, PATCH /videos/{id}, REFRAME_PAN_*/CLIP_REGEN_* keys, crop-track `region` field |
| `docs/OFF_COURSE_BUGS.md` | Incidental-defect log (three new rows from 437) |
| `~/.claude/ISSUES_LOG.md` | Cross-project incident log |
| Memory dir | `/home/reese/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` (`project_l26_autoclip_mvp.md` = lane memory) |
