# LEFT_OFF.md — CreatorClip Session Handoff

**Last updated:** 2026-08-05 (afternoon) · **Branch:** `main` — feature commit `6150754` + this close-out docs commit on top · working tree clean after close-out · in sync with `origin/main`; `staging` synced
**Prod:** `https://autoclip.studio` — **Issue 395 direct-to-R2 upload IS LIVE AND VERIFIED** (deploy run 31015798993 green, `/health` 200, first real 273 MB upload completed in 58 s, 8 clips rendered). L26 A→B pipeline live since morning (DB `0055`).
**Git note:** `stash@{0}` = the owner's own pre-session "wip LEFT_OFF before research-branch checkout" stash — popping it onto this file WILL conflict; resolve by hand or drop deliberately.

> Source-of-truth docs live in `docs/`. This file orients and points to them — it is NOT a source of truth.

---

## CURRENT FOCUS

**Clip-quality follow-ups from the first live A→B run: Issues 427–430** (`docs/issues.md`
§ "L26 follow-ups — filed from the first live A→B run + clip audit (2026-08-05)"). The upload
wall is gone (Issue 395 shipped and verified same-day); the first real end-to-end run then
surfaced four verified clip-quality defects, each filed with evidence + acceptance criteria.
The user explicitly wants to work these in the next session.

## → NEXT ACTIONS (in order)

1. **Issue 428 first — mid-sentence clip opens (M, clip-quality core).** The worst defect: clip
   `84b362b0` opens at "really think it's gonna happen" where the audio says "I **don't** really
   think…" — a meaning-INVERTING cut. 5/8 clips open on fragments; LLM clip `960d3931` carries
   ~8 s outro pre-roll AND runs 110 s (>90 s target); `616ad186`'s suggested_hook describes
   content ~8 s in, not the actual open. Fix = sentence-boundary snap for `setup_start_s` (the
   Deepgram segments in `transcripts.segments_jsonb` carry punctuation + word timestamps), LLM
   window sentence-snap + length clamp, hook generated from the final window's first ~5 s.
   Add an eval scenario (mid-sentence-open fixture; SCENARIO_FLOOR bump). **Run the full issue
   workflow (CHECK brief first) — this touches `clip_engine/`, so the eval harness gates it.**
2. **Issue 427 — captions render ON the speaker's face (S, most user-visible).** Karaoke word at
   ~50% frame height sits on the face in every audited frame. Move baseline to ~68–75% height,
   never overlap the detected face box, consider 2–3-word grouping.
3. **Issue 429 — near-duplicate clips (S).** `84b362b0` (1438–1481) is fully inside `960d3931`
   (1390–1500); both rendered (IoU ≈ 0.39 slipped under NMS). Post-ranking containment/diversity
   pass; keep the signal-priority union pre-scoring untouched (`extract_candidates` must stay
   byte-identical — see gotchas).
4. **Issue 430 — source-layout chrome in the crop (M, interacts with Issue 422).** Produced
   podcast sources carry their own logo/name-chip/social-banner; the full-height static crop
   slices through all of it. Needs camera-region detection before the 9:16 composition. Consider
   sequencing with/after the Issue 422 reframe staging work.
5. **Remaining Issue-395 live drills** (user-driven, opportunistic during their stress tests):
   >2 GB file · mid-upload reload (parts must not re-send) · >60-min upload crossing the session
   expiry (pause banner → re-login new tab → Retry resumes) · declared-25 GB → 413 · abort leaves
   no incomplete uploads in the R2 dashboard.
6. **Parked from before:** Issue 422 staging checklist (mediapipe×numpy step 0,
   `docs/DEPLOYMENT.md`) · L25 Batch C (393) · operator punch-list (`docs/GO_LIVE.md`): #29 OAuth
   verification · #26/#28 friend beta · #282 uptime monitor · #255 key escrow · user's Cloudflare
   Security→Events lookup for the 2026-08-05 ~13:1x UTC 403 (closure only — failure class gone).

### Local gate incantations

```bash
export PATH=/home/reese/.nvm/versions/node/v22.17.1/bin:$PATH && hash -r   # node 26 breaks jsdom — bit AGAIN 2026-08-05; .nvmrc fix suggested in OFF_COURSE_BUGS
redis-cli ping || redis-server --daemonize yes --save '' --appendonly no
# backend:  .venv/bin/python -m pytest -m "not integration" -p no:langsmith -q
# frontend: run from frontend/ (root npx = cached-Playwright ERR_INTERNAL_ASSERTION)
# Layer 0:  .venv/bin/python .claude/skills/production-assessment/scripts/run_layer0.py
# eval:     any clip_engine/ change → the eval harness (tests/test_clip_engine.py) gates it
```

---

## WHAT WORKS NOW (all verified live 2026-08-05 — do not re-investigate)

**Issue 395 direct-to-R2 upload, in production:**
- 273 MB (286,674,239 B) uploaded in **58 s** browser→R2 (`transport=multipart` in the
  `video_uploaded` event) vs 40+ min on the dead proxy path. The morning's failure is fully
  explained: the file was ~3× Cloudflare's 100 MB proxy body cap (cap identical on Free and Pro;
  the zone is now Pro) — the edge 403'd it; the app never saw it. New path bypasses the zone.
- Bucket CORS **already set** on `autoclip-studio` via `scripts/r2_set_cors.py` (PUT +
  Content-Type + ExposeHeaders ETag + MaxAge 3600, `get_bucket_cors` echo verified) — do NOT
  re-run unless the origin changes.
- cloudflared **on http2** ("will use 'http2' as primary protocol" in its logs).
- Worker correctly fills `duration_s` + reclassifies `kind` at ingest (row created NULL by
  design). Client-side duration probe returned None for this OBS-style file — advisory only,
  fallback worked; watch whether that's common (UX-only concern).
- Pipeline after upload: ingest → diarized Deepgram transcript → video_context → signals → 8
  clips + clips_ready email ~2.5 min post-upload → metadata batch (8 clips, one call) → renders.

**Clip audit method (repeatable — this is how 427–430 were evidenced):**
- Clip rows: `ssh creatorclip-vm "cd /opt/autoclip && docker compose -f docker-compose.prod.yml exec -T postgres psql -U creatorclip -d creatorclip -c \"SELECT … FROM clips WHERE video_id='…'\""` — origin/principle live in `signals_jsonb->>'origin'` / `->>'principle'` (NO `origin` column on clips).
- Transcript windows: `jsonb_array_elements(segments_jsonb->'segments')` on `transcripts` (shape: `{"source":"deepgram","segments":[{start,end,text,words:[{word,start,end,speaker,speaker_confidence}]}]}`).
- Visual: presign via `docker compose exec -T app python -c "from worker.storage import presigned_download_url; …"` → curl locally → `ffmpeg -ss … -frames:v 1` → Read frames as images. Loudness: `ffmpeg -af volumedetect`.
- Renders verified clean: 1080×1920 h264/aac 48 kHz stereo, −16.2 dB mean / −5.5 dB max, no clipping, durations match DB windows.

**Session gates (commit `6150754`):** backend **2795/0** (new `tests/test_videos_multipart_upload.py`,
27 tests) · frontend (node 22) **637/637**, `tsc -b` clean, eslint 0 errors · Layer-0 ALL GREEN
(ruff 0 · mypy 0 · coverage 84.03 · bandit 0/0 · pip-audit 0) · deploy chain green end-to-end.

---

## THE ARC THAT LED HERE (one day, compressed)

1. Morning: user stress-tested the L26 A→B flow with a real 273 MB podcast video → upload crawled
   40+ min through the cloudflared tunnel, then failed with a Cloudflare-edge 403 the app never
   logged; session expired mid-upload (60-min JWT). Diagnosis pinned all three mechanisms.
2. `/issue-workflow` on the already-specced **Issue 395** (presigned direct-to-R2 multipart,
   BETA BLOCKER): live R2/Cloudflare research, 2 explore + 1 design agent, plan approved with
   3 user decisions (full issue w/ Uppy · cloudflared→http2 · quota replaces the MB cap).
3. Built same-day: backend endpoints + storage helpers + worker hunk + CSP, headless-Uppy
   frontend, CORS script, tests, docs, DECISIONS (10 rulings). Pushed `6150754`; deploy green;
   CORS set; http2 confirmed.
4. User re-ran the real upload: 58 s, 8 clips. Clip audit (DB + transcript + frames + audio)
   → **Issues 427–430 filed** with evidence. This close-out ends the session; next context
   starts on 428.

---

## KEY COORDINATES & FACTS

| Thing | Value |
|---|---|
| Repo | `github.com/reese8272/creatorclip` · prod = VM (`ssh creatorclip-vm`, standing permission), `/opt/autoclip`, docker-compose, Cloudflare tunnel |
| Deploy chain | push to `main` → docker-publish → deploy (staging gate → prod). **A failed image build silently SKIPS deploy** — `gh run list` after every push. After merge: `git push origin main:staging` |
| Audit video | `videos.id = e290e6f4-12b9-4d6c-a4c2-d56542502740` (26:57, 1617.2 s, standalone upload, key `source/{creator}/24150513….mp4`) — source purges 72 h after ingest (~2026-08-08); re-upload if drills need it later |
| Audit clips (full ids in DB) | `85e8f48d…` top scorer 0.50 · `616ad186…` hook-mismatch · `4b1269e5…` · `84b362b0…` meaning-inverting open · `31882569…` question-open (GOOD example) · `960d3931…` LLM 110 s pre-roll · `311596f0…` pronoun-open · `2893e613…` LLM early-story |
| Upload endpoints | `/videos/uploads/config·POST·/{uid}/parts/{n}/presign·/{uid}/parts·/{uid}/complete·/{uid}/abort` (routers/videos.py); key shape `source/{creator.id}/{yt-id|uuid4hex}{.mp4|.mov|.mkv|.webm|.m4v}` → 403 otherwise; 25 MiB fixed parts; presign expiry 900 s |
| Complete ordering | dedupe(409)+balance(402) BEFORE CompleteMultipartUpload; NoSuchUpload+row → idempotent re-return; IntegrityError → 409 WITHOUT object delete (deterministic keys) |
| Frontend uploader | `frontend/src/lib/uploader.ts` (singleton, session-expiry pause via `redirectOn401:false`) · `hooks/useUploader.ts` · `public/uppy-sw.js` (classic-script copy of golden-retriever SW — sync on Uppy upgrades) · GoldenRetriever guarded behind localStorage+indexedDB presence |
| Caption render path (Issue 427 target) | wherever the karaoke captions are burned in the render pipeline (worker render task / ffmpeg filtergraph) — currently ~50% frame height, single word |
| Engine seams (428/429 targets) | `setup_start_s` backward-look (word-snap today) · LLM moment validation (Issue 415 code) · NMS union pre-scoring — `extract_candidates` BYTE-IDENTICAL constraint stands; add post-ranking passes, never touch the union |
| JWT / session | `JWT_EXPIRY_MINUTES=60`, no refresh (refresh endpoint = deliberate non-goal of 395; separate product decision if wanted) |
| Cloudflare | zone Pro (upgraded 2026-08-05); 100 MB proxy body cap UNCHANGED on Pro; presigned R2 URLs bypass the zone entirely |
| Python / Node | `.venv/bin/python` for everything backend · node **v22.17.1** via PATH export for everything frontend |
| Secrets | `.env` on the VM (R2_*, tunnel token, etc.) — names only, never values |

---

## CONSTRAINTS & GOTCHAS (carried forward — all still true)

- **All prior L26 gotchas hold**: `analyze_video_context` never raises (LLM failure →
  `context_skipped` → signal-only clips); `extract_candidates` byte-identical (eval-harness
  green-by-construction rests on it — 429's fix must be post-ranking); per-shot speaker tracks;
  trim-filmstrip sizing exclusion; `&&` not `;`; ruff format reflows between edits; GUC
  `NULLIF(current_setting('app.creator_id', true), '')::uuid`; integration/RLS tests are
  CI-only (no local Docker).
- **Node 22 for frontend gates** — bit twice now; consider committing `.nvmrc` + `engines`
  (OFF_COURSE_BUGS 2026-08-05) as a warm-up chore next session.
- **Any `clip_engine/` change (428/429) runs the eval harness** and likely ADDS scenarios —
  budget for `SCENARIO_FLOOR` bumps, and remember CLAUDE.md's Phase-1 CHECK applies (research
  sentence-segmentation / caption-safe-area standards before building).
- e2e mock keeps uploads in proxy mode on purpose — multipart specs must stub the R2 origin.
- The permission classifier sometimes blocks read-only `docker compose exec` forms (ls/sh -c)
  while allowing `exec -T app python -c …` and `exec -T postgres psql` — use those shapes.
- The owner sometimes powers the droplet off intentionally — check before treating prod-down as
  an incident (memory: `project_live_deployment_topology`).

---

## OPEN, LOGGED, NOT FIXED

Canonical list `docs/OFF_COURSE_BUGS.md`. Top: node-26 gotcha needs its structural fix ·
pre-migration safety dump unset (Issue 256) · `clips/` storage not creator-scoped (erasure gap) ·
`test_response_models` peaks/stream gap (pre-existing, environment-dependent).

---

## POINTERS

| Doc | Purpose |
|---|---|
| `docs/issues.md` | Work queue — **§ "L26 follow-ups" (427–430) is the active batch**; § 395 status line tracks the remaining live drills |
| `docs/DECISIONS.md` 2026-08-05 | Issue-395 10 rulings + verification + drill list |
| `docs/PROJECT_STATE.md` top entry | 395 build + same-day deploy/verification + audit summary |
| `docs/SOT.md` / `docs/COMPLIANCE.md` / `.env.example` | Updated for the new upload path |
| `docs/CLIPPING_PRINCIPLES.md` | Principles registry — 427/428 acceptance cites it |
| `~/.claude/ISSUES_LOG.md` | Cross-project incident log |
| Memory dir | `/home/reese/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` (`project_l26_autoclip_mvp.md` = lane memory, updated with today's arc) |
