# LEFT_OFF.md — CreatorClip Session Handoff

**Last updated:** 2026-08-05 · **Branch:** `main` @ `403b2bd` + **UNCOMMITTED Issue-395 build** (deliberate — push to main auto-deploys prod; owner decides when)
**Prod:** `https://autoclip.studio` — L26 live (DB `0055`); still running the OLD proxy upload until this ships.
**Git note:** `stash@{0}` = the owner's own "wip LEFT_OFF before research-branch checkout" — popping it onto this rewritten file WILL conflict; resolve by hand or drop if obsolete.

> Source-of-truth docs live in `docs/`. This file orients and points to them — it is NOT a source of truth.

---

## CURRENT FOCUS

**Issue 395 — presigned direct-to-R2 multipart upload (BETA BLOCKER) — BUILT 2026-08-05, all local
gates green, NOT yet committed/deployed.** Trigger: the owner's live stress test hit every predicted
proxy-upload failure (40-min tunnel crawl, Cloudflare-edge 403 the app never logged, mid-upload
session expiry). Full architecture: `docs/DECISIONS.md` 2026-08-05 (10 numbered rulings).
What's new: 6 `/videos/uploads/*` endpoints (stateless sessions, key-shape isolation,
quota-before-complete), `worker/storage.py` multipart helpers, CSP `connect-src` + R2 origin,
headless Uppy 5 frontend (drag-drop queue, honest progress, session-expiry pause→resume,
`public/uppy-sw.js`), `scripts/r2_set_cors.py`, cloudflared → HTTP/2, `UPLOAD_MAX_FILE_GB=20`
(minutes quota is the real gate; `UPLOAD_MAX_MB` now proxy-paths-only).

## → NEXT ACTIONS (in order)

1. **Commit + push the Issue-395 build** (owner's call — push deploys prod via docker-publish→deploy).
   Everything modified/untracked in the tree belongs to it EXCEPT `stash@{0}` (see git note).
2. **Deploy-day sequence:** deploy green → `ssh creatorclip-vm`, from `/opt/autoclip` run the app
   container's copy or locally `python3.12 scripts/r2_set_cors.py https://autoclip.studio` (needs
   R2_* env; echoes the policy back; ~30 s propagation) → verify cloudflared logs report http2.
3. **Live Phase-4 acceptance drills** (checklist in `docs/issues.md` § 395 + DECISIONS entry):
   >2 GB upload end-to-end → pipeline done; mid-upload network kill (only failed parts retry);
   reload-resume (part count only grows); browser-restart ghost re-select; >60-min upload crossing
   session expiry → pause → re-login (new tab) → Retry resumes; declared-25 GB → 413; abort leaves
   no lingering incomplete uploads (R2 dashboard); browser console: part PUT preflight OK and ETag
   readable (CORS ExposeHeaders proof — the classic failure of this pattern).
4. **Owner lookups still open from the incident:** Cloudflare dashboard → Security → Events,
   ~13:15–13:30 UTC 2026-08-05, method POST — names the rule behind the 403 (zone is Pro; the 100 MB
   proxy body cap is unchanged by Pro and moot for the new path).
5. **Then resume the prior tail:** Issue 422 staging checklist (mediapipe×numpy step 0) · L25
   Batch C (393 next) · operator punch-list (`docs/GO_LIVE.md`): #29 OAuth verification ·
   #26/#28 friend beta · #282 uptime monitor · #255 key escrow.

### Local gate incantations

```bash
export PATH=/home/reese/.nvm/versions/node/v22.17.1/bin:$PATH && hash -r   # node 26 breaks jsdom (35 phantom vitest failures) — bit AGAIN this session; .nvmrc fix suggested in OFF_COURSE_BUGS
redis-cli ping || redis-server --daemonize yes --save '' --appendonly no
# backend:  .venv/bin/python -m pytest -m "not integration" -p no:langsmith -q
# frontend: run from frontend/ (root npx = cached-Playwright ERR_INTERNAL_ASSERTION)
# Layer 0:  .venv/bin/python .claude/skills/production-assessment/scripts/run_layer0.py
```

---

## WHAT WORKS NOW (verified on this tree — do not re-investigate)

| Gate | Value |
|---|---|
| Backend unit lane | **2795 passed / 71 skipped / 0 failed** (incl. new 27-test `tests/test_videos_multipart_upload.py`) |
| Frontend vitest (node 22) | **637 passed / 88 files** (624 → 637; structural gates included) |
| `tsc -b` · eslint | clean · 0 errors (1 pre-existing warning `useStageStream.ts:100`) |
| Layer 0 | ALL GREEN — ruff 0 · mypy 0 · coverage **84.03** · module floors pass · bandit 0/0 · pip-audit 0 |
| Alembic | unchanged — single head `0055` (Issue 395 is migration-free by design) |
| Playwright e2e | NOT run locally this session — mock-api serves `/videos/uploads/config` → proxy mode so e2e never hits R2; CI covers it |

Key test-fallout notes: `InlineUploadFlow.test.tsx` FakeXHR harness replaced with a `useUploader`
mock; `Dashboard.test.tsx` label updated ("Video files to upload"); legacy
`test_videos_upload_streaming.py` untouched and green (proxy endpoint lives on for dev + OBS).

---

## KEY COORDINATES & FACTS (Issue-395 additions; prior table still true)

| Thing | Value |
|---|---|
| Upload transport pick | `GET /videos/uploads/config` → `multipart` (STORAGE_BACKEND=r2) / `proxy` (local dev) — ONE Uppy queue UI either way |
| Isolation boundary | `_validate_upload_key` (routers/videos.py): key must be `source/{creator.id}/{yt-id\|uuid4hex}{.mp4\|.mov\|.mkv\|.webm\|.m4v}` else 403; R2 binds uploadId→key; no Video row until /complete |
| Complete ordering | dedupe(409) + balance(402) BEFORE CompleteMultipartUpload (top-up never re-uploads); NoSuchUpload+existing row → idempotent re-return; IntegrityError → 409 WITHOUT object delete (deterministic keys — winner may own the object) |
| Part size | fixed 25 MiB (`_UPLOAD_PART_SIZE_BYTES`) — satisfies R2's equal-parts rule; presign expiry 900 s; sign endpoint 1200/min |
| Session expiry mid-upload | upload endpoints use `api(..., redirectOn401:false)` → pause + banner; part PUTs are presigned (no session needed); parts kept 7 days (R2 auto-abort) |
| uppy-sw.js | classic-script copy of @uppy/golden-retriever's SW (shipped file has ESM marker) — keep in sync on Uppy upgrades |
| Prod runtime numbers | JWT_EXPIRY_MINUTES=60 (unchanged — refresh endpoint deliberately out of scope); Cloudflare zone now **Pro** (upgraded 2026-08-05; 100 MB proxy cap unchanged) |

---

## CONSTRAINTS & GOTCHAS (carried forward — all still true)

- All prior L26 gotchas in the 2026-08-05-morning LEFT_OFF revision remain true (analyze_video_context
  never raises; extract_candidates byte-identical; per-shot speaker tracks; trim-filmstrip sizing;
  frontend cmds from `frontend/`; `&&` not `;`; ruff format reflows; GUC NULLIF; integration tests
  need Docker/CI).
- **Node 22 for ALL frontend gates** — now bitten twice; consider the .nvmrc/engines fix
  (OFF_COURSE_BUGS 2026-08-05) before it bites a third time.
- The e2e mock deliberately keeps uploads in proxy mode — if a spec ever needs multipart, it must
  stub the R2 origin route itself.

---

## OPEN, LOGGED, NOT FIXED

Canonical list `docs/OFF_COURSE_BUGS.md` — unchanged from the morning revision, plus the re-logged
node-26 gotcha. Top of mind: pre-migration safety dump unset (Issue 256) · `clips/` storage not
creator-scoped (erasure gap) · `test_response_models` peaks/stream gap (pre-existing).

---

## POINTERS

| Doc | Purpose |
|---|---|
| `docs/issues.md` § 395 | The issue + acceptance state (all boxes checked except the deploy-gated >2 GB drill) |
| `docs/DECISIONS.md` 2026-08-05 | The 10 rulings + verification numbers + drill list |
| `docs/PROJECT_STATE.md` top entry | This build's close-out summary |
| `docs/SOT.md` / `docs/COMPLIANCE.md` / `.env.example` | Updated for the new write path + config |
| `~/.claude/ISSUES_LOG.md` | Cross-project incident log |
