# LEFT_OFF.md — CreatorClip Session Handoff

**Last updated:** 2026-08-12 · **Branch:** `main` @ `c138e93` · **working tree CLEAN** ·
**0 ahead / 0 behind `origin/main`; `origin/staging` synced to the same SHA; no stray branches.**
**Prod:** `https://autoclip.studio`, alembic **`0058 (head)`** (no new migrations this session).
**⚠ The `c138e93` deploy was STILL PUBLISHING at session end — confirm it landed before anything else.**

> Source-of-truth docs live in `docs/`. This file orients and points to them — it is NOT a source
> of truth.

---

## CURRENT FOCUS

**Billing was discovered to have never worked — in production, for 10 weeks — and the fix is
merged but NOT yet proven.** Nothing else matters until a real purchase settles. Everything else
this session (451, 452) is shipped and green.

## → NEXT ACTION

1. **Confirm the `c138e93` deploy landed.** `gh run list --limit 5` — a failed image build
   **silently skips** the deploy step. Then prove the code is actually in the container, not just
   in the registry:
   ```bash
   ssh creatorclip-vm 'docker exec autoclip-app-1 grep -c RequestsClient /app/billing/stripe_client.py'   # expect 1+
   ```
2. **Prove Stripe actually works** — read-only, safe, no charge, and it is the whole ballgame:
   ```bash
   ssh creatorclip-vm 'docker exec autoclip-app-1 python -c "
   from billing.stripe_client import list_recent_paid_sessions
   print(\"OK:\", len(list_recent_paid_sessions(48)))"'
   ```
   **This exact call failed twice tonight in two different ways.** If it raises, read
   "THE TWO STRIPE DEFECTS" below before touching anything — the error text is misleading.
3. **Buy the smallest pack on prod for real.** Minutes must credit. Until this happens,
   `docs/GO_LIVE.md`'s billing gate stays **RED** — it cannot go green on code alone. Also confirm
   `reconcile_stripe_ledger` stops raising on its next beat.
4. **Expect Issue 454 during step 3.** Clicking Buy a *second* time in the same tab — especially on
   a different pack — is likely to fail. **That is 454, not a regression of 453/455.** It is filed
   and diagnosed, not built.
5. **⏰ Time-boxed — video `7e988321`'s source purges 2026-08-13 19:23 UTC.** The ONLY thing that
   dies with it is the **live superchat-mask render** (448's end-to-end proof).
   *Correction to the previous handoff: the 444 idempotency drill is NOT on this clock —
   `purge_stale_source_media` deletes source media only; clip rows and rendered R2 objects
   survive.* Sequence if you want it: set `OVERLAY_BAND_DETECT_ENABLED` in the VM `.env` → restart
   app+worker → run/await the hourly backfill (`overlay_spans_jsonb` is NULL for this video) →
   re-render ranks 3/13. The owner deferred this once already; letting it lapse is a defensible call.

### Local gate incantations

```bash
export PATH=/home/reese/.nvm/versions/node/v22.17.1/bin:$PATH && hash -r   # node 26 = 35 phantom jsdom fails
redis-cli ping || redis-server --daemonize yes --save '' --appendonly no
# backend:  .venv/bin/python -m pytest -m "not integration" -p no:langsmith -q   # baseline 2976/0
# frontend: run from frontend/ — npx vitest run                                  # baseline 657/657
#           AND `npm run build` — that is `tsc -b && vite build` and it TYPE-CHECKS THE TESTS.
#           `npx tsc --noEmit` does NOT, and passed while CI failed on 12 type errors (2026-08-12).
# Layer 0:  .venv/bin/python .claude/skills/production-assessment/scripts/run_layer0.py
# ruff:     CI runs `ruff check .` AND `ruff format --check .` — Layer 0 only runs the first
# eval:     any clip_engine/ change → tests/test_clip_engine.py (SCENARIO_FLOOR=23, 25 fixtures)
```

**Ship via a PR, never a direct commit to `main`** — `ci.yml` has no `push` trigger, so a direct
commit runs **zero** of the ~12 gating jobs.

---

## THE TWO STRIPE DEFECTS (read before debugging any Stripe error)

`stripe==11.4.0`'s `HTTPXClient` is **unusable for real requests**, for two independent reasons
stacked in one constructor. Fixing the first only reveals the second, with a completely different
error message — which is exactly what happened tonight, mid-session, after PR #82 had already
merged and deployed green.

| # | Defect | Symptom | Fixed by |
|---|---|---|---|
| 1 | `allow_sync_methods` defaults to **False**, leaving the sync `httpx.Client` unbuilt | `RuntimeError: ...cannot be used for synchronous requests` | #82 (Issue 453) |
| 2 | SSL context built as `create_default_context(capath=ca_bundle_path)` — `capath` wants a **directory**, the bundle is a **.pem file** ⇒ **0 CA certs load** | bare `APIConnectionError("...A ConnectError was raised")` — **reads like a network outage or firewall, and is not** | #84 (Issue 455) |

Measured: `capath` → **0** certs, `cafile` → **135**. Current transport is
`stripe.RequestsClient(timeout=STRIPE_TIMEOUT_S)` — the SDK's own default, which takes the timeout
directly and passes the bundle as `verify=<file>` (correct usage).

**Do NOT "modernise" this back to `HTTPXClient`.** `billing/stripe_client.py` carries a comment
naming both defects for exactly this reason.

**How defect 2 was isolated, if a similar one ever recurs:** from *inside the prod app container*,
`socket.gethostbyname("api.stripe.com")` resolved and plain
`httpx.get("https://api.stripe.com/v1/balance")` returned **401 (reachable)** at the same moment the
SDK client could not connect at all. That ruled out network/DNS/egress and pointed at the client's
own SSL context.

---

## WHAT WORKS NOW (verified — do not re-investigate)

**Merged + deployed this session** (`b2a71ff`, `d43e3b3`; `c138e93` deploy in flight):

- **Issue 453 (#82)** — `allow_sync_methods=True`. Necessary, **not sufficient** — see above.
- **Issue 455 (#84)** — `RequestsClient`. **Verified against LIVE Stripe from the prod container**
  with a read-only `checkout.sessions.list` *before* the change was written (returned 1 session).
  This is the only Stripe call known to have succeeded from our client in 10 weeks.
- **Issue 451 (#83)** — a rendered clip can be re-rendered from the actions rail, reusing the shared
  `useClipRender` ladder. A purged source explains itself instead of offering a button that 409s.
- **Issue 452 (#83)** — title/caption **wrap** in the focused review view; the native `title=`
  tooltip is gone (it clipped long strings exactly like the truncation it was escaping).

**From the previous session, still true:** Issues 26, 445, 448 (inert, flag off), 450 all shipped;
prod alembic `0058`.

**Baselines:** backend **2976/0** · frontend **657/657** · eval 25 scenarios/100 % · coverage 84.18,
`clip_engine` 93.03 (floor 91.0). All three PRs passed **12/12** CI gates.

---

## THE ARC THAT LED HERE

1. Session opened on the post-#81 handoff. The owner asked to work Issues 451/452 and to
   "figure out my stripe issue — it says it can't process it at this time".
2. Planning predicted the cause was a stale checkout idempotency key. **The prod log said
   otherwise**: `HTTPXClient was initialized with allow_sync_methods=False`, three times, matching
   the owner's clicks. `grep -c "billing checkout_session"` over 168 h returned **0** — no Checkout
   Session has ever been created. `reconcile_stripe_ledger` had been raising every beat since
   2026-05-31 (`334d1f7`, the Issue 106 commit that added the timeout).
3. #82 shipped that fix, green, deployed. **Billing still did not work** — the error had merely
   changed shape. Defect 2 (empty CA trust store) was underneath it; #84 fixed it properly.
4. 451 + 452 built and merged alongside (#83, one rebase — both PRs inserted at the same anchor in
   `DECISIONS.md`; both entries kept, gates re-run before merge).
5. `main` merged, `staging` fast-forwarded to match.

---

## KEY COORDINATES & FACTS

| Thing | Value |
|---|---|
| Repo | `github.com/reese8272/creatorclip` · prod = VM (`ssh creatorclip-vm`, standing permission), `/opt/autoclip`, docker-compose, Cloudflare tunnel |
| Prod containers | `autoclip-app-1`, `autoclip-worker-1`, `autoclip-beat-1`, `autoclip-render-worker-1` (**`docker compose` from `/opt/autoclip` fails — no compose file there; use `docker exec <container>` / `docker logs <container>` directly**) |
| Deploy chain | push to `main` → Docker publish → Deploy to production (staging migration gate → prod). **A failed image build silently SKIPS deploy** — `gh run list` after every push |
| Merged this session | #84 (455), #83 (451+452), #82 (453) |
| Audited video | `7e988321-2265-4e22-85bd-0e9ffd583f84` — **source expires 2026-08-13 19:23 UTC** |
| Creator | Backboard Media `eb9af967-5d2f-4063-a05e-9f4f070ce840` |
| Live flags (VM `.env`) | `ACTIVE_SPEAKER_REFRAME_ENABLED=true` · `CAMERA_REGION_DETECT_ENABLED=true` · `REFRAME_MIN_MAPPING_CONFIDENCE=0.2` · **`OVERLAY_BAND_DETECT_ENABLED` absent → false** |
| Next free issue number | **456** |
| Frozen fixtures | `tests/fixtures/superchat/` (36 frames) + `tests/fixtures/reframe_seats/` (12), both with provenance READMEs — the ONLY reproduction once the source purges |
| Secrets | `.env` on the VM — names only, never values |

---

## CONSTRAINTS & GOTCHAS

- **A green test suite proved nothing about billing.** Every billing test mocks at or above
  `create_checkout_session`, so **nothing in the suite touched the transport** — 2976 passing tests
  coexisted with a total revenue outage for 10 weeks. `tests/test_sdk_timeouts.py` now asserts *the
  transport can issue a real request* rather than either specific bug, because pinning only defect 1
  is precisely what let defect 2 through.
- **`scripts/doctor.py --full` green-lights Stripe without exercising our client.** `doctor.py:405`
  probes with a raw `httpx.get`, so it validated the API *key*, never the SDK *transport* — which is
  how `docs/GO_LIVE.md:71` came to cite "Stripe live-verified" through a total outage. Logged in
  `docs/OFF_COURSE_BUGS.md` (2026-08-12, SEV3, open). The other `_live_*` probes have the same shape.
- **`docs/GO_LIVE.md` billing gate is RED**, reverted from GREEN. Stage A is now
  **15 GREEN / 6 CODE-GREEN / 10 OPEN / 1 RED**. It returns to green only on a settled purchase.
- **RLS blindness.** Prod connects as `creatorclip_app` with no `BYPASSRLS` and `FORCE ROW LEVEL
  SECURITY` on every tenant table, so a query with `app.creator_id` unset returns **zero rows and
  no error**. `creators` is the RLS-exempt bootstrap — read it first, then
  `SELECT set_config('app.creator_id', <uuid>, false)` before every query.
- **A celery-direct re-render CANNOT re-render.** The worker skips a clip that already has a
  `render_uri`. `POST /clips/{id}/render` owns the reset (Issue 353). Issue 451 now surfaces that
  endpoint in the UI, so hand-replicating the reset should no longer be necessary.
- **Never detect overlay bands on a RENDERED clip.** Burned-in captions are themselves a bright
  lower-frame band. Detect on the source.
- **`mapping.confidence` is a MARGIN ratio** `(best−second)/best`, not a correctness estimate. Do
  not gate on it (450).
- **`onAdvance` in `YourCall` serves BOTH a verdict and the plain "Next clip" skip.** Invalidating
  `review-clips` there drops the rated clip while the index also moves — silently skipping a clip.
  Only `clip-counts` is invalidated.
- **`npx tsc --noEmit` is NOT the frontend type gate.** CI runs `npm run build` (`tsc -b && vite
  build`), which type-checks the TEST files too.
- **Beware first-occurrence string replaces in large files.** Verify with a grep for the symbol after.
- **Do not "restore" things DECISIONS deliberately removed:** EMA smoothing (436), the camera-region
  height ceiling (439), speaker following on `face_pan` (440), coordinators/pronouns in the
  weak-opener list (441), re-validating the consensus median (443), a `triage=` filter on
  `GET /videos/{id}/clips` (444), the shortlist as a FILTER (445), **and `stripe.HTTPXClient` (455)**.
- **Migrations:** any DATA-manipulating migration needs an `if context.is_offline_mode():` branch —
  CI renders every migration with `alembic upgrade --sql`, which has no connection.
- Owner sometimes powers the droplet off intentionally — check before treating prod-down as an incident.

---

## OPEN, LOGGED, NOT FIXED

Canonical list: `docs/OFF_COURSE_BUGS.md` + `docs/issues.md`. Top:

- **Issue 454** — the checkout `intent_id` is scoped to the **browser tab**, not the purchase, so a
  second attempt (especially on a different pack) replays Stripe's idempotency key with different
  params → 400 → 502 → one generic toast. Filed with full research and an approach; **not built**.
  Was invisible behind the outage; will surface the moment checkout works.
- **Issue 449** — `snap_start`'s inter-sentence-pause exemption bypasses 441's weak-opener guard
  (rank 4 opens on "Yeah."). Diagnosed and reproducible; not built.
- **Issue 447** — the Keep pile needs a finish line (rendered → downloaded → published).
- **`doctor.py`'s client-bypassing probes** (2026-08-12) · **direct-to-main bypasses CI entirely** ·
  **Issue 442** (`style_preset["background"]` accepted, never applied) · 502 root cause on the VM
  never investigated · pre-migration safety dump unset (Issue 256).

---

## POINTERS

| Doc | Purpose |
|---|---|
| `docs/issues.md` | Work queue — 451/452/453/455 done; **449, 454 open**; next free number **456** |
| `docs/GO_LIVE.md` | Go/no-go scorecard — **billing RED**; #28 (friend smoke) still the other blocker |
| `docs/DECISIONS.md` | 2026-08-12: 455 (+ a CORRECTION block on the 453 entry), 452, 445, 450, 448 |
| `docs/PROJECT_STATE.md` | Close-outs for 453/455 + 451/452 |
| `docs/SOT.md` | Architecture |
| `docs/OFF_COURSE_BUGS.md` | Incidental-defect log |
| `docs/ACCESS.md` | Beta tester setup |
| Memory dir | `/home/reese/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` |
