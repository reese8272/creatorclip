# LEFT_OFF.md — CreatorClip / AutoClip Session Handoff

**Last updated:** 2026-08-14 · **Branch:** `docs/catalog-sync-live-evidence` @ `2db1798`
**Working tree:** 2 modified files, both the OWNER's in-flight work, deliberately not committed by
the last session — `static/privacy.html` + `tests/test_static.py` (LLC-rebrand sweep, Issue 488).
**Prod:** `https://autoclip.studio`, deployed at `1221fb8` (2026-08-14 17:39 UTC, staging gate →
prod, both green). **Open PR:** **#115** (`docs/catalog-sync-live-evidence` → `staging`), 13/13 CI
green, **ready to merge**.

> ⚠️ `main` and `staging` are BRANCH-PROTECTED (8 required checks, linear history). Direct pushes to
> `main` are rejected — **everything goes through a PR**, and the documented path is
> `feature → staging → main`. Merging to `main` triggers a production deploy.

> Source-of-truth docs live in `docs/`. This file orients and points to them — it is NOT a source of
> truth. If a number here disagrees with `docs/GO_LIVE.md`, **the doc wins**.

---

## CURRENT FOCUS

**The friend beta (#28).** The last hard *code* blocker was found, fixed, deployed and live-verified
on 2026-08-14. What remains is almost entirely an **operator checklist** — things only the owner can
do in a browser/console — plus one genuinely unbuilt UI (Issue 445).

### → NEXT ACTION

1. **Merge PR #115** (`gh pr merge 115 --merge`), then open `staging → main` and merge that. It
   records the catalog-sync live evidence and flips that gate GREEN. 13/13 checks already pass.
   *Merging to `main` deploys — this one is docs-only, so it is a no-op deploy, but it will still
   run the full pipeline.*
2. **Decide what to do with the 2 uncommitted files** in the working tree. They are the owner's
   LLC-rebrand edits (`CreatorClip` → `AutoClip` in `privacy.html`, plus a `test_static.py` guard
   that no retired brand name survives on any legal page). They were deliberately kept out of every
   commit this session. They belong with **Issue 488**.
3. **Work the operator track below** — that is now the critical path to the beta, not code.

---

## WHAT WORKS NOW (verified — do not re-investigate)

### The catalog-sync outage is fixed and PROVEN on prod

`_FIELDS_PLAYLIST_ITEMS` requested `snippet(resourceId/videoId,…)` while `list_channel_videos`
filtered items on `resource_id.get("kind") == "youtube#video"`. A Google `fields` spec returns **only
the properties it names**, so `kind` was absent, every item was dropped, and `sync_video_catalog`
returned early — HTTP 200, no exception, `"Synced N video(s)"` in the log. **No creator imported a
single video between 2026-06-24 (`38317eb`, Issue 260) and 2026-08-14.**

Live result on prod, creator `eb9af967-…`, task `12430bde`, driven through the **real HTTP endpoint**
(`POST /creators/me/catalog/sync` with a minted session cookie, not a Celery shortcut):

| | before | after |
|---|---|---|
| `origin=catalog` videos | 0 | **21** (10 long / 15 Shorts) |
| `video_metrics` with `engagement_rate` | 0 | **21** |
| retention-curve points | 0 | **2100** |
| `GET /creators/me/data-gate` | `0/0 ready:false` | **`6 long / 15 shorts, ready:true`** |

### Also shipped and green in the same release (`1221fb8`)

- **Issue 490** — Beat refresh committed once per creator, so a mid-loop sub-budget exhaustion rolled
  back the whole run, never reached `sync_audience_data`, and left a NULL timestamp that the
  `NULLS FIRST` ordering used to pin big channels to the queue head forever. Now: audience data
  first, incremental commits, stalest-first ordering, stamp on partial progress.
- **Issue 491** — seven prompt builders injected `"No DNA profile available yet."` under
  instructions telling the model to cite channel patterns, then appended "grounded in your channel
  data" unconditionally. Now the block is **omitted** and `grounding_disclaimer()` keys the claim off
  the same signal. Thumbnail concepts no longer fabricate `based_on_pattern`; hook analysis
  distinguishes "no baseline" from "no drop"; `analyze-performer` won't invent figures or cache an
  ungrounded row under `dna_version=NULL`.
- **Issue 492** — caption-hooks, explanation and the chat `suggest_clip_titles` tool all passed the
  **whole video's opening** instead of the clip's own window (Issue 414 was never applied to them).
- **Issue 493** — in-app YouTube reconnect on Profile, where the weekly `reauth_required`
  notification had been pointing at a page with no reconnect control. `/auth/me` now returns
  `youtube_connected` + `youtube_expires_at`.
- **Issue 494** — `GET /me/thumbnail-patterns` (billed multimodal vision call) and
  `POST /me/dna/build` had **neither** `require_flag` nor `require_budget`.

Full detail: `docs/issues.md` §489–495, `docs/DECISIONS.md` 2026-08-14, `docs/PROJECT_STATE.md`.

---

## ⛳ WHAT IS LEFT TO COMPLETE THE BETA

`docs/GO_LIVE.md` is the canonical scorecard — **read it, don't trust this summary**. As of
2026-08-14: **Stage A = 35 gates · 20 GREEN · 5 CODE-GREEN · 10 OPEN · 0 RED.**

### A. Operator track — the real critical path (nobody but the owner can do these)

Ordered by consequence, per `docs/runbooks/255-258-dr-durability.md`:

| # | What | Why it's ordered here | Runbook |
|---|---|---|---|
| **255** | **Secrets escrow — DO THIS FIRST.** Copy `TOKEN_ENCRYPTION_KEY`, `JWT_SECRET_KEY` and a snapshot of `/opt/autoclip/.env` to **two independent** legs (password manager **and** GCP Secret Manager). | Without it a perfect Postgres restore yields useless ciphertext — every user's OAuth tokens unrecoverable. It is the prerequisite that makes every other backup worth having. **Never store these inside the backup they protect.** | `docs/RUNBOOKS.md:578-589` |
| — | **Rotate the exposed Anthropic key.** New key in console → VM `.env` → `doctor.py --full` green → **then** revoke the old one. | A known-exposed credential on a billed API. | `docs/SECRETS.md:219-222` |
| **256/257** | **Nightly encrypted PG backups.** Create `creatorclip-backups`; set `BACKUP_R2_BUCKET` + `BACKUP_ENCRYPTION_KEY`; install cron `7 3 * * * cd /opt/autoclip && ./scripts/backup_pg.sh`. | No backups exist today. | `docs/RUNBOOKS.md:590-606` |
| — | **Run the restore drill.** `python3 scripts/reapply_erasures.py` afterwards is **mandatory**, not optional. | It is what stops a restore resurrecting data a user asked to have erased. Record the RTO. | `docs/RUNBOOKS.md:641-648` |
| **258** | **R2 Object Lock**, Compliance mode ≥14d — **not** Governance (admin-overridable, therefore not tamper-proof) — plus per-prefix lifecycle. | Reconcile the windows against right-to-erasure (#254); also closes that CODE-GREEN row. | `docs/RUNBOOKS.md:596-601` |
| **288** | **Redis durability.** cron `27 3 * * * ./scripts/backup_redis.sh`, then the restart drill. *Staging Redis is intentionally ephemeral — do not "fix" it.* | The Celery broker currently does not survive a restart. | `docs/RUNBOOKS.md:711-714` |
| **286** | **Cloudflare edge rate limit** on `/auth/*` — `preauth-rate-limit`, 10 req/min per IP, Managed Challenge. Keep `/health` **out** of the expression. | The verify loop must show the challenge **and** `docker compose logs app` showing no request flood — that is what proves the block happened at the edge, not in the app. | `docs/EDGE_SECURITY.md:26-79` |
| **246** | **`MAILING_ADDRESS`** — a real physical postal address (PO box or CMRA mailbox; it is printed publicly in every lifecycle email footer). | Until set, **all lifecycle email is intentionally SKIPPED** (`config.py:1007-1010`, enforced at `worker/tasks.py:4556`). That is the correct fail-safe, not a bug. **Not required for a friend beta**; required the moment the audience is not friends. | — |

### B. Code track — one genuinely unbuilt item

- **Issue 445 — the three-pile triage UI.** Tracked in `docs/issues.md` (Lane L27) — the last session recorded 6 unchecked ACs; **re-read the section before planning, the numbering has drifted**. Strangers hit the
  review queue on their **first** upload, and today reviewed state does not survive a reload.
  Four design questions are still open in the issue body — **run a real CHECK phase first.**
- **Issue 484** — the meaning-inverting cold open (a clip that opens on `"feel like Percy Butler
  is…"` when the speaker said *"don't feel like"*). Highest-impact known clip-quality defect.
- **Issue 495** — the deferred list from the 2026-08-14 audit: brand-kit fields that cannot be
  cleared; the `captions_enabled` toggle no renderer reads; `Profile.tsx` hardcoded `"—"` stats; the
  fully-built **data export with no UI** (GDPR Art. 15/20); `is_rewatch_spike` +
  `captions_available` never written; `push_enabled`; unvalidated `_upsert_style_field`; the
  `AskSurfaceTabs → /analysis` dead end.

### C. The #28 friend smoke itself

Now **unblocked**. The criterion is *the full pipeline exercised on prod over 48 h* — the invite is
not the gate. Warn the friend about two expected things so they don't read as breakage:

1. The **"Google hasn't verified this app"** interstitial at consent (expected while #29 is
   unsubmitted).
2. **Weekly reconnect** — Google expires Testing-mode refresh tokens after 7 days. There is now a
   real reconnect card on Profile (Issue 493), and the app warns 2 days out.

### D. Stage B (public launch) — start the clock now

- **Issue #29 — Google OAuth verification.** 1–4 week external review; **it is the only item with a
  clock you cannot compress.** Submit the READ-ONLY scope set only (`openid`, `userinfo.email`,
  `userinfo.profile`, `youtube.readonly`, `yt-analytics.readonly` — `youtube/oauth.py`). **Do NOT
  include `youtube.upload`**: it drags in the heavier YouTube API compliance audit (#194 keeps it a
  separate, later submission). The 100-user cap is **not** why this matters — the 7-day token expiry
  is, and it is independent of user count.
- Remaining Stage-B gates (8 OPEN): #261 load profile, #236 SLOs, #282 status page (re-opened for a
  non-friend audience), #326 Grafana/Sentry activation → unblocks #291 cost alerts, key-rotation dry
  run, final security review, pricing beyond minute packs, and the #30 sign-off.

---

## THE ARC THAT LED HERE

1. **2026-08-13** — CI-integrity checkpoint closed; `main`/`staging` branch-protected.
2. **2026-08-14 (morning)** — billing went RED → GREEN: a real purchase credited 200 minutes through
   the webhook after four stacked defects (transport → Cloudflare OWASP edge block → wrong endpoint
   URL → branding). Ledger then claimed **#28 was the sole remaining blocker**.
3. **2026-08-14 (this session)** — owner asked three questions: *"sync says 0 shorts and 0 videos"*,
   *"why reconnect every 7 days"*, *"why can't I generate titles and hooks"*. They resolved to **one
   root cause** (the `fields`/`kind` contract drift) plus a class of related defects.
4. Fixed, guarded with a contract test **verified to fail on the pre-fix spec**, shipped via
   `#113 → staging`, `#114 → main`, deployed `1221fb8`, and **live-verified with a real sync**.
5. The ledger claim in (2) was **wrong and has been corrected**: #28 could never have passed while a
   friend's first sync returned nothing.

---

## KEY COORDINATES & FACTS

| Thing | Value |
|---|---|
| Prod URL | `https://autoclip.studio` |
| VM (DigitalOcean) | `147.182.136.107`, deploy dir `/opt/autoclip`, compose at `/opt/autoclip/src/docker-compose.prod.yml` |
| Containers | `autoclip-app-1`, `autoclip-worker-1`, `autoclip-render-worker-1`, `autoclip-beat-1`, `autoclip-postgres-1`, `autoclip-redis-1`, `autoclip-cloudflared-1` |
| Prod DB | `docker exec autoclip-postgres-1 psql -U creatorclip -d creatorclip` |
| Owner creator id | `eb9af967-5d2f-4063-a05e-9f4f070ce840` ("Backboard Media", channel `UCNU5Tnt0xp7YtHNPgxDrSIw`) |
| Deployed commit | `1221fb8` |
| Image | `ghcr.io/reese8272/creatorclip:latest` |
| Deploy chain | push to `main` → **Docker publish** → (`workflow_run`) → **Deploy to production** → staging gate → prod smoke + auto-rollback |
| Local test env | **Use `.venv/bin/python`** or `scripts/ci_local.sh` — see gotchas |
| Node | 22.17.1 (`.nvmrc`); node 26 breaks jsdom |
| Secrets (names only) | `TOKEN_ENCRYPTION_KEY`, `JWT_SECRET_KEY`, `ANTHROPIC_API_KEY`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `BACKUP_ENCRYPTION_KEY` — in VM `/opt/autoclip/.env` + GitHub secrets. **Never read or print values.** |

---

## CONSTRAINTS & GOTCHAS

1. **⚠️ RUN TESTS FROM `.venv`, NOT SYSTEM PYTHON.** This burned the last session badly. System
   `python3.12` has **fastapi 0.115.4** against the pinned **0.137.1**, and its `mypy` cannot import
   the `pydantic` plugin — so mypy aborts before checking anything and the Layer-0 gate reports a
   **vacuous `ok 0`**. Under system python the suite showed 4 phantom failures and pip-audit showed
   77 phantom CVEs; under `.venv` it is **3170 passed / 0 failed** and mypy/bandit/pip-audit all 0.
   Two OFF_COURSE_BUGS rows were filed on that bad data and have since been **removed as
   non-issues**. Use `scripts/ci_local.sh --fast` (it prefixes `PATH` with `.venv/bin`).
2. **FastAPI 0.137.1 defers `include_router`** into `_IncludedRouter` objects, so `app.routes` holds
   almost no `APIRoute`. Any test that walks routes naively iterates **zero** routes and passes
   vacuously — this is the documented cause of `tests/test_response_models.py` being unreliable, and
   it bit the new billed-route guard in `tests/test_flags.py`. Resolve via
   `effective_route_contexts` (they carry the prefixed path + merged dependant); see that test.
3. **`deploy.yml` triggers on `workflow_run`, not `push`.** After merging to `main` there is a gap
   while **Docker publish** builds. A naive "latest deploy run" check will match the *previous*
   deploy and report a stale success — this nearly happened. **Pin any deploy check to your SHA.**
4. **A green intermediate layer is not a working feature.** This is the standing lesson of both the
   billing outage and the catalog outage, and it recurred three times in one session. Neither a
   passing suite, nor a success log line, nor a green pipeline is evidence a feature does its job —
   only the feature's own output is. Read the deployed constant out of the **running container**
   before trusting a deploy.
5. **Counters must count writes, not attempts.** `"Synced 4 new video(s)"` over a dead feature is
   what hid the outage for seven weeks. `sync_video_analytics` now returns whether metrics were
   written; keep it that way.
6. **A `fields=`/projection string and the code that parses the response must change together.**
   `tests/test_data_api.py` now applies the real spec to the fixture before parsing so drift fails
   CI. A fixture that encodes an *unfiltered* response is worse than no fixture.
7. **Branch protection**: `enforce_admins: false` is load-bearing for the staging sync — do not
   "harden" it without reading Issue 145.
8. **Never revert Stripe to `HTTPXClient`** — two stacked defects, 10-week total checkout outage
   (`billing/stripe_client.py`, DECISIONS 2026-08-12). `RequestsClient` only.
9. **Before any fresh-upload drill**: run `python3.12 scripts/r2_set_cors.py https://autoclip.studio`.
   The `ExposeHeaders ETag` is load-bearing — without it multipart completes stall at 100%.
10. **Before debugging anything non-trivial**, grep the cross-project log first:
    `grep -i "<symptom>" ~/.claude/ISSUES_LOG.md`. The `fields=`-strips-a-required-key failure mode
    is recorded there with its ruled-out hypotheses.

---

## POINTERS

| Doc | What it owns |
|---|---|
| `docs/GO_LIVE.md` | **The canonical launch scorecard** — Stage A/B gates, owners, evidence. Start here. |
| `docs/PROJECT_STATE.md` | Session log + what's done/in-flight. |
| `docs/issues.md` | The work queue (Wave × Lane × Batch). Next free issue number: **496**. |
| `docs/DECISIONS.md` | Every deviation + why. Grounding-honesty rationale is the 2026-08-14 entry. |
| `docs/OFF_COURSE_BUGS.md` | Incidental defects found while doing something else. |
| `docs/RUNBOOKS.md` · `docs/SECRETS.md` · `docs/EDGE_SECURITY.md` · `docs/ACCESS.md` | Operator procedures for the track above. |
| `docs/SOT.md` · `docs/COMPLIANCE.md` · `docs/CLIPPING_PRINCIPLES.md` | Architecture, ToS/retention posture, named principles. |
| `~/.claude/ISSUES_LOG.md` | Cross-project root causes (search before debugging). |
| `~/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` | Session memory index. |
