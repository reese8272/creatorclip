# LEFT_OFF.md — CreatorClip Session Handoff

**Last updated:** 2026-08-12 · **Branch:** `main` @ `7e3582a` · **working tree CLEAN** ·
**0 ahead / 0 behind `origin/main`; `origin/staging` at the same SHA; no stray branches.**
**Prod:** `https://autoclip.studio`, alembic **`0058 (head)`**. Deploy of `7e3582a` was in flight
at session end — confirm with `gh run list` before assuming it landed.

> Source-of-truth docs live in `docs/`. This file orients and points to them — it is NOT a source
> of truth.

---

## CURRENT FOCUS

**Everything built this session is merged (PR #81, `7e3582a`).** The one thing that matters next is
that a human actually uses the new review flow on prod — it has never been exercised against a real
API response.

## → NEXT ACTION

1. **Confirm the `7e3582a` deploy landed** — `gh run list --limit 5`; a failed image build
   **silently skips** the deploy step. Then `curl -s https://autoclip.studio/health`.
2. **Have the creator click through `/review` on prod.** 445's piles read `clip.triage`,
   which the API returns but the frontend type had never declared — **it has only been exercised
   against mocks.** This is the highest-value unverified thing in the repo right now.
3. **⏰ Time-boxed — video `7e988321`'s source purges 2026-08-13 19:23 UTC** (`ingest_done_at`
   2026-08-10 19:23 + 72 h). Two things die with it:
   - **444's idempotency box** — needs the SAME clip triaged twice with the same verdict. The
     creator kept one clip and dropped a *different* one, which tests the sync path, not the no-op.
     Current triage on that video: **10 pending / 2 kept / 1 dropped**.
   - **A live superchat-mask render.** `OVERLAY_BAND_DETECT_ENABLED` is **not in the VM `.env`**
     (defaults false) and `videos.overlay_spans_jsonb` is **NULL** for this video — detection runs
     at ingest and this video predates 448. Sequence: set the flag → restart app+worker → trigger
     (or wait for) the hourly backfill → re-render ranks 3/13. The owner deferred this
     ("let's change the superchat blur after we figure everything else out").
4. **Then Issues 452 → 451** (both filed today, small): title/caption still truncate in the
   FOCUSED review view (445 only fixed the pile rows), and a rendered clip has no re-render
   affordance.

### Local gate incantations

```bash
export PATH=/home/reese/.nvm/versions/node/v22.17.1/bin:$PATH && hash -r   # node 26 = 35 phantom jsdom fails
redis-cli ping || redis-server --daemonize yes --save '' --appendonly no
# backend:  .venv/bin/python -m pytest -m "not integration" -p no:langsmith -q   # baseline 2975/0
# frontend: run from frontend/ — npx vitest run                                  # baseline 653/653
#           AND `npm run build` — that is `tsc -b && vite build` and it TYPE-CHECKS THE TESTS.
#           `npx tsc --noEmit` does NOT, and passed while CI failed on 12 type errors (2026-08-12).
# Layer 0:  .venv/bin/python .claude/skills/production-assessment/scripts/run_layer0.py
# ruff:     CI runs `ruff check .` AND `ruff format --check .` — Layer 0 only runs the first
# eval:     any clip_engine/ change → tests/test_clip_engine.py (SCENARIO_FLOOR=23, 25 fixtures)
```

**Ship via a PR, never a direct commit to `main`** — `ci.yml` has no `push` trigger, so a direct
commit runs **zero** of the ~12 gating jobs.

---

## WHAT WORKS NOW (verified — do not re-investigate)

**Merged + deployed today (`643e8d3`, prod alembic `0058`):**

- **Issue 26 CLOSED** — OAuth consent screen configured and **verified from the live app, not the
  console**: `/auth/login` 302s to Google with `redirect_uri=https://autoclip.studio/auth/callback`
  (character-exact) and exactly five scopes (`openid`, `userinfo.email`, `userinfo.profile`,
  `youtube.readonly`, `yt-analytics.readonly`) — **no `youtube.upload`**. A live round trip advanced
  `youtube_tokens.updated_at`; `creators` stayed at 6 (row reused). Protected routes 401.
  Cross-creator isolation re-verified (`creatorclip_app`, `BYPASSRLS=false`, 0 rows with the GUC
  unset, 0 foreign rows). **Residual: the ≥2-test-user count was never confirmed by the operator.**
- **Issue 448 SHIPPED (inert)** — `clip_engine/overlay_bands.py` + migration `0058`
  (`videos.overlay_spans_jsonb`). Masks a superchat with a time-gated
  `split → crop → boxblur → overlay`. Proven on the real source: rank 3 re-rendered clean before
  the span, donor name/amount/message unreadable inside it. **Flag off in prod.**
- **Issue 450 SHIPPED and PROVEN on delivered media** — rank 1's framing moved **x=230 → x=1104**;
  the clip now holds the person actually speaking. `speaker_map.speaking_track_for_span()`.
- **Issue 445 SHIPPED (`7e3582a`)** — three piles as tabs in `/review`, active pile in the URL.
  The queue is now every untriaged clip (the shortlist ORDERS it rather than truncating it —
  reverses Issue 377, see DECISIONS), and the Dashboard badge counts `pending` instead of
  `rendered`. Kept/dropped are lists with wrapping titles.

**Stage A beta:** `docs/GO_LIVE.md` is **16 GREEN / 6 CODE-GREEN / 10 OPEN**, and **#28 (friend
smoke) is the only hard blocker left.**

**Measured, not estimated** (keep — the source is about to vanish):
- Superchat occupies **885–914 s and 930–1006 s = 107 s of 1617 s (6.6 % of runtime)**; rank 3
  carries it 11.6 s, rank 13 **25.3 s**.
- Rank 1: 2 face tracks at cx **381.2** / **1257.5**, `crop_w` 309, `mapping.confidence` 0.084,
  mouth energy **0.1456** (right, speaking) vs **0.0536** (left).

---

## THE ARC THAT LED HERE

1. A fresh upload (`7e988321`) was audited — it is the **same recording** as the 2026-08-07
   baseline (`duration_s = 1617.216667` on both), making it a true A/B. 438/440/443 verified;
   441's cold-open half and 440's framing half failed → Issues 449/450; a superchat defect → 448.
2. 26 → 448 → 450 built and shipped in that order, evidence frozen first because of the 72 h clock.
3. The creator then reviewed clips on prod and reported three things: no re-render button, the
   review queue claiming "all reviewed" after 3 clips while the dashboard said 27, and truncated
   titles/captions. All three were real; two are fixed in PR #81, two are filed as 451/452.

---

## KEY COORDINATES & FACTS

| Thing | Value |
|---|---|
| Repo | `github.com/reese8272/creatorclip` · prod = VM (`ssh creatorclip-vm`, standing permission), `/opt/autoclip`, docker-compose, Cloudflare tunnel |
| Deploy chain | push to `main` → Docker publish → Deploy to production (staging migration gate → prod). **A failed image build silently SKIPS deploy** — `gh run list` after every push |
| Merged today | #81 (445 + dashboard count + 451/452 filings), #80 (26/448/450), #79, #78 |
| Audited video | `7e988321-2265-4e22-85bd-0e9ffd583f84` — **source expires 2026-08-13 19:23 UTC** |
| Creator | Backboard Media `eb9af967-5d2f-4063-a05e-9f4f070ce840` |
| Live flags (VM `.env`) | `ACTIVE_SPEAKER_REFRAME_ENABLED=true` · `CAMERA_REGION_DETECT_ENABLED=true` · `REFRAME_MIN_MAPPING_CONFIDENCE=0.2` · **`OVERLAY_BAND_DETECT_ENABLED` absent → false** |
| Next free issue number | **453** |
| Frozen fixtures | `tests/fixtures/superchat/` (36 frames) + `tests/fixtures/reframe_seats/` (12), both with provenance READMEs — the ONLY reproduction once the source purges |
| Baselines | backend **2975/0** · frontend **653/653** · eval 25 scenarios/100 % · coverage 84.18, `clip_engine` 93.03 (floor 91.0) |
| Secrets | `.env` on the VM — names only, never values |

---

## CONSTRAINTS & GOTCHAS

- **RLS blindness.** Prod connects as `creatorclip_app` with no `BYPASSRLS` and `FORCE ROW LEVEL
  SECURITY` on every tenant table, so a query with `app.creator_id` unset returns **zero rows and
  no error**. `creators` is the RLS-exempt bootstrap — read it first, then
  `SELECT set_config('app.creator_id', <uuid>, false)` before every query. Bit this session again.
- **A celery-direct re-render CANNOT re-render.** The worker skips a clip that already has a
  `render_uri` ("already rendered — skipping"). `POST /clips/{id}/render` owns the reset (Issue
  353 sets `render_status=pending`, `render_uri=NULL`; 359c restores on enqueue failure). To force
  one by hand, replicate that reset — and snapshot the prior URI first.
- **Never detect overlay bands on a RENDERED clip.** Burned-in captions are themselves a bright
  lower-frame band; a rendered-clip scan flagged two visually-clean clips. Detect on the source.
- **`mapping.confidence` is a MARGIN ratio** `(best−second)/best`, not a correctness estimate. It
  is structurally small when both faces are on screen the whole time. Do not gate on it (450).
- **`onAdvance` in `YourCall` serves BOTH a verdict and the plain "Next clip" skip**
  (`YourCall.tsx:127` and `:305`). Invalidating `review-clips` there drops the rated clip while the
  index also moves — silently skipping a clip. Only `clip-counts` is invalidated.
- **Beware first-occurrence string replaces in large files.** Twice this session a `.replace(…, 1)`
  patched the wrong function (`worker/tasks.py`'s poster backfill; `Review.tsx`'s `ReviewClipView`).
  Verify with a grep for the symbol afterwards.
- **`npx tsc --noEmit` is NOT the frontend type gate.** CI runs `npm run build` (`tsc -b && vite
  build`), which type-checks the TEST files too. Local `tsc --noEmit` passed while CI failed on 12
  errors (2026-08-12). Run `npm run build` before pushing frontend changes.
- **`EmptyStatePrompt` requires an action** — deliberately, so a dead-end empty state cannot
  type-check. If a new empty state will not compile, add the way to fill it rather than working
  around the type.
- **The cold-first-run vitest flake recurred** (650/651 cold, 651/651 on two immediate re-runs,
  `environment ~300s` vs ~207s). The name went uncaptured *again* — logged in
  `docs/OFF_COURSE_BUGS.md` with the suggestion to make verbose reporting structural.
- **Do not "restore" things DECISIONS deliberately removed:** the EMA smoothing (436), the
  camera-region height ceiling (439), speaker following on the `face_pan` rung (440), coordinators/
  pronouns in the weak-opener list (441), re-validating the consensus median (443), a `triage=`
  filter on `GET /videos/{id}/clips` (444 — it would corrupt `ClipImpression`), **and the shortlist
  as a FILTER (445 — it is now ordering only)**.
- **Migrations:** any DATA-manipulating migration needs an `if context.is_offline_mode():` branch —
  CI renders every migration with `alembic upgrade --sql`, which has no connection. `0058` is
  additive so it needed none.
- Owner sometimes powers the droplet off intentionally — check before treating prod-down as an
  incident.

---

## OPEN, LOGGED, NOT FIXED

Canonical list: `docs/OFF_COURSE_BUGS.md` + `docs/issues.md`. Top:
- **Issue 449** — `snap_start`'s inter-sentence-pause exemption bypasses 441's weak-opener guard
  (rank 4 opens on "Yeah."). Diagnosed and reproducible; **not built**.
- **Issues 451 / 452** — no re-render affordance; truncation in the focused review view.
- **Issue 447** — the Keep pile needs a finish line (rendered → downloaded → published). 445 built
  the pile; this is what makes it a destination.
- **Direct-to-main bypasses CI entirely** — structural fix still unfixed.
- **Issue 442** (`style_preset["background"]` accepted, never applied) · **502 root cause on the VM**
  never investigated · pre-migration safety dump unset (Issue 256).

---

## POINTERS

| Doc | Purpose |
|---|---|
| `docs/issues.md` | Work queue — 445 done in PR #81; **449, 451, 452 open**; next free number **453** |
| `docs/GO_LIVE.md` | Go/no-go scorecard — Stage A 16 GREEN / 6 CODE-GREEN / 10 OPEN; **#28 is the last blocker** |
| `docs/DECISIONS.md` | Three 2026-08-12 entries (450 seat mapping + split-screen reversal, 448, 445 shortlist reversal) + 2026-08-11 (448) |
| `docs/PROJECT_STATE.md` | Close-outs for 26, 448, 450 |
| `docs/SOT.md` | Architecture — `overlay_bands.py` and `overlay_spans_jsonb` documented |
| `docs/ACCESS.md` | Beta tester setup — updated for the new Google Auth Platform console UI |
| `docs/OFF_COURSE_BUGS.md` | Incidental-defect log |
| Memory dir | `/home/reese/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` |
