# GO_LIVE.md — Consolidated Go/No-Go Launch Scorecard (Issue #303)

**This file is the canonical launch ledger.** The three older gate lists
(`CLAUDE.md` "Pre-Public-Launch Requirements", `docs/PROJECT_STATE.md` "Pre-Public-Launch
Gates", `docs/COMPLIANCE.md` "Pre-Public-Launch Compliance Gates") now point here; status
is maintained ONLY here. Gates reference their issue id in `docs/issues.md` — gate text is
never duplicated. Shape follows the Google SRE launch-checklist pattern (domain-grouped
scorecard + explicit sign-off + abort criterion), per DECISIONS 2026-07-02.

**Two stages** (v1 scope lock, DECISIONS 2026-06-26 — ≤100-user private beta first):

- **Stage A** — invite the first friends (≤100-user private beta on the VM / Render blueprint).
- **Stage B** — public launch (Issue #30): everything Stage A plus the public-only gates.

**Status legend** — `GREEN` (evidence verified), `CODE-GREEN` (shipped + locally verified;
a live/staging/operator verification step remains), `OPEN` (not done, or done but
unverified). A gate is GREEN only with evidence; when in doubt it stays OPEN.

**Launch order** (condensed from the #303 phase plan): DR foundations (#256–258, #288) →
CI + migration policy (#270, #294–297) → deploy mechanics (#298/#271, #24, #25) → staging
verification pass → **Stage A BETA** (#26, #28) → prod prereqs (#29, #261, #236, #296) →
**Stage B public** (#30).

---

## Stage A — ≤100-user private beta

### Security & Isolation

| Gate (question) | Action item | Owner | Status today | Evidence / signal |
|---|---|---|---|---|
| Are prod secrets provisioned, unique, and off-git? | None — verified | build | GREEN (2026-07-29) | #24 all ACs verified live post-outage-recovery: `/docs` 404, container env exact (ENV=production, ALLOWED_ORIGINS/OAUTH_REDIRECT_URI/APP_BASE_URL locked), `.env` untracked + no secret values in history, GH secrets present, deploy pipeline proven by the Deploy-1 run (staging gate → prod, 2026-07-29) |
| Is per-creator tenant isolation structurally enforced in prod? | None — RLS role split active | build | GREEN | #343 (verified live 2026-06-30); `docs/DEPLOYMENT.md` "RLS one-time setup" |
| Has the exposed Anthropic key been rotated? | Rotate in the provider console + VM `.env` | operator | OPEN | `LEFT_OFF.md` operator checklist |
| Is edge rate limiting live on `/auth/*`? | Apply the Cloudflare rule + run the 429 verify loop | operator | OPEN | #286 (config committed); `docs/EDGE_SECURITY.md` |
| Are per-creator app rate limits + pre-job quotas live? | Live/staging 429 smoke via `scripts/live_smoke.py` | operator | CODE-GREEN | #228 (shipped 2026-06-24; residual smoke), #312 (async storage), #321 (brief quota) |
| Are LLM spend caps + the cost circuit breaker armed? | None — trip drill executed green | operator | GREEN (2026-08-13) | #290; `billing/spend_guard.py`; `docs/RUNBOOKS.md` trip/reset section. **Evidence:** `staging-drills` run [31709917464](https://github.com/reese8272/creatorclip/actions/runs/31709917464), `spend-trip` leg, against the live `ccstage` stack: one oversized probe spend breached the global daily cap and flipped `llm_generation` OFF (`event=flag_flipped … reason=spend cap tripped: global_daily`); the **second** breach latched with no error (SETNX latch held, exactly one flip); the RUNBOOKS manual reset then cleared latch+counters and restored the flag |
| Can risky subsystems be killed without a deploy? | None — flip proof executed green | staging-verify | GREEN (2026-08-13) | #284; `flags.py`, `scripts/flags.py`. **Evidence:** `staging-drills` run [31709917464](https://github.com/reese8272/creatorclip/actions/runs/31709917464), `flags-flip` leg, against the live `ccstage` stack: with `llm_generation` OFF the gated LLM route returned **503**, and after re-enabling it returned **202** — no deploy, no restart. The re-enable is asserted via bounded polling because a flip is only guaranteed visible after one `FLAG_TTL_S` (30 s) window in the serving process; asserting immediately raced that cache and was what made this drill fail on 2026-08-13 before PR #102 |
| Are `TOKEN_ENCRYPTION_KEY` / `JWT_SECRET_KEY` / `.env` escrowed off-box? | Copy the 3 secrets to 1Password + GCP Secret Manager | operator | OPEN | #255 (runbooks/docs done); `docs/SECRETS.md` |

### Compliance & Privacy

| Gate (question) | Action item | Owner | Status today | Evidence / signal |
|---|---|---|---|---|
| Is YouTube data-retention/refresh ToS-compliant? | None | build | GREEN | Issue 75b (30-day partial-staleness purge); `docs/COMPLIANCE.md` |
| Are ToS + Privacy Policy live, linked, and accurate? | Confirm the deployed pages during #28 | build | GREEN | Issue 14; #252 (GDPR/CCPA rewrite); footer links (Wave-6 Fix B) |
| Does account deletion revoke tokens + purge media/data? | Prod exercise happens inside #28 | build | GREEN | #158 (+#247/#248/#249); verified in-repo 2026-07-02: `routers/auth.py` erasure helper incl. Google `/revoke` POST |
| Is the `yt-dlp` path guarded (off by default, own-content only)? | None | build | GREEN | Verified in-repo 2026-07-02: `youtube/ingest.py:89` gate on `config.py:421` `YTDLP_ENABLED=False` |
| Do we request only necessary OAuth scopes? | Keep the login set read-only; `youtube.upload` stays incremental-consent only | build | GREEN | Verified in-repo 2026-07-02: `youtube/oauth.py` `SCOPES` (read-only) + separate `PUBLISH_SCOPE` (#194) |
| Is the OAuth consent screen configured with beta test users? | **Confirm the test-user count is ≥2** — the only unverified part | operator | GREEN (2026-08-11), one residual | #26. Configured on the current "Google Auth Platform" console UI: **Branding** app name **AutoClip** (not "CreatorClip" — every user-facing surface, the ToS and the privacy policy all say AutoClip, and Google's Stage-B review checks that consistency), home/privacy/ToS links live (all 200), authorized domain `autoclip.studio`; **Audience** External + Testing; **Data Access** exactly five scopes, Restricted empty. Verified from the live app rather than the console: `GET /auth/login` 302s to `accounts.google.com` with `client_id=742666675967-…`, `redirect_uri=https://autoclip.studio/auth/callback` (character-exact), `access_type=offline`, and precisely `openid` + `userinfo.email` + `userinfo.profile` + `youtube.readonly` + `yt-analytics.readonly` — **no `youtube.upload`**. Live OAuth round trip completed 2026-08-11: `youtube_tokens.updated_at` advanced `2026-08-10 23:36:45` → `2026-08-11 18:29:58`, still 5 scopes, and `creators` stayed at 6 (the existing row was reused, not duplicated). Protected routes 401 without a session (`/videos`, `/auth/me`, `/clips/counts`, `/videos/clips/counts`). Cross-creator isolation re-verified live: `creatorclip_app`, `BYPASSRLS=false`, tenant tables return **0 rows with the `app.creator_id` GUC unset** (fails closed) and **0 foreign rows** per creator across `videos`/`clips`/`youtube_tokens`/`clip_feedback`. **Residual:** the ≥2-test-user count was never confirmed — if it is currently just the owner, add a friend before #28 |
| Is the regulatory posture shipped (COPPA age gate, accessibility statement, GPC)? | None | build | GREEN | #300, #301, #302 |
| Do restores honor prior erasures (backup-erasure stance)? | Confirm R2 lifecycle/Object-Lock numbers in the dashboard | operator | CODE-GREEN | #254; `scripts/reapply_erasures.py`; `docs/RUNBOOKS.md` DR steps |
| Is lifecycle (commercial-leaning) email CAN-SPAM compliant? | **Set `MAILING_ADDRESS`** to a valid physical postal address — a street address, a USPS-registered PO box, or a registered CMRA mailbox. It is printed in every lifecycle email footer, so it becomes public. **Until it is set, all lifecycle email is intentionally SKIPPED** (`config.py:752`), which is the correct fail-safe, not a bug. Not required for the friend beta. | operator | OPEN (fail-safe active) | #246 code-complete (templates, `run_lifecycle_scan` beat task, shared 48h cap, `email_lifecycle` opt-out, RFC 8058 one-click, 13 tests). CAN-SPAM: opt-out honored ≤10 business days, mechanism live ≥30 days, valid physical postal address required on commercial mail |

### Reliability & DR

| Gate (question) | Action item | Owner | Status today | Evidence / signal |
|---|---|---|---|---|
| Do nightly encrypted PG backups exist and restore? | Create bucket/cron; run the staging restore drill | operator | OPEN | #256, #257 (both code-complete; drill pending); `scripts/backup_pg.sh` |
| Is the backup bucket tamper-proof (Object Lock + lifecycle)? | Apply R2 Bucket Lock + lifecycle config | operator | OPEN | #258 (decision + docs done) |
| Does the Redis broker survive a restart (durability + backup)? | Deploy compose change, install 03:27 cron, run the drill | operator | OPEN | #288 (code-complete); `scripts/backup_redis.sh`; `docs/RUNBOOKS.md` |
| Is there an incident-response front door? | None | build | GREEN | #283; `docs/INCIDENT_RESPONSE.md` (severity ladder + runbook index) |

### Deploy mechanics

| Gate (question) | Action item | Owner | Status today | Evidence / signal |
|---|---|---|---|---|
| Does every prod deploy pass a data-bearing staging gate first? | None — first runs completed | build | GREEN | #298 (+#271 fix); gate blocked 3 real problems then passed W3 clean, 2026-07-02/03 (runs on PRs #46–#50); seed idempotency fixed PR #50 |
| Is migration safety enforced (Squawk, timeouts, expand/contract, rollback runbook)? | None | build | GREEN | #270, #294; `docs/MIGRATIONS.md` |
| Do deploys verify the critical journey and tag every promotion? | None | build | GREEN | #295, #297 |
| Are all external APIs provisioned with `/health` green? | None — verified | build | GREEN (2026-07-29) | #25: `doctor.py --full` on the VM = **30 ok / 0 fail** (Anthropic, Voyage, Deepgram, R2, Stripe all live-verified), `/health` all-ok, key-leak grep over 30d app+worker logs = 0 hits; LLM E2E nightly green daily |
| Can a new user upload a real-world file at all? | Run the three #395 acceptance drills on prod | operator | **CODE-GREEN** | #395 — **added to this ledger 2026-08-13; it was a flagged BETA BLOCKER that had never been a row here** (`docs/issues.md:1105`). Built + deployed + partially live-verified 2026-08-05 (commit `6150754`): first real multipart upload = 273 MB in 58 s vs a 40-min proxy crawl, and bucket CORS set via `scripts/r2_set_cors.py` (echo verified). It matters because `UPLOAD_MAX_MB = 500` while a 20-min 1080p OBS recording is routinely 1–3 GB — this breaks the *first thing a new user does*. **Three drills remain, and only one of them has its own checkbox** — the reload-resume debt is buried inside a *checked* box at `docs/issues.md:1162` and the session-expiry drill is named only in the status line, so all three are promoted here: (a) >2 GB file end to end — evidence: ________ · (b) reload mid-upload → resume without re-sending parts (note the "ghost file" re-select path, DECISIONS 2026-08-05) — evidence: ________ · (c) 60-min JWT expires mid-upload → pause → re-login → resume — evidence: ________ |
| Are the W1/W2 staging-verify residuals exercised? | None — all three legs executed green | staging-verify | GREEN (2026-08-13) | `.github/workflows/staging-drills.yml`. **Evidence:** run [31727428785](https://github.com/reese8272/creatorclip/actions/runs/31727428785) — `flags-flip: re-enabled -> 202`, `spend-trip: manual reset restored the flag`, and `rate-limit: 20 cheap 404 probes then 429 at request #21 (binding limit=20)`. That last line is the point: the leg previously passed **vacuously** (an already-spent quota made the first probe 429, so `first_429 == 0` and the guard degenerated to `all([]) == True`), and now proves #228's actual property — 429 arrives *only after* the quota, not merely at some point. Four defects had to be fixed to get an honest run: the vacuous assertion (#105), a per-drill event loop vs. the loop-bound Redis singleton (#106), asserting the daily 60 instead of the binding 20/hour burst (#107), and the real culprit — `spend-trip` leaking the 1 h spend cool-down, whose `require_budget` 429 masqueraded as a rate-limit trip and even made a later `flags-flip` log `re-enabled -> 429. PASS` (#108, +#109 clean-start) |
| Has the full pipeline run end-to-end on prod with real friends for 48h? | Execute the #28 beta smoke + friend onboarding — the Stage-A capstone | operator | OPEN | #28 (blocked by #24/#25/#26) |

### Observability & Cost

| Gate (question) | Action item | Owner | Status today | Evidence / signal |
|---|---|---|---|---|
| Are logs/metrics/traces + error tracking live? | Verify Grafana Cloud + Sentry ingest on the live SaaS side | operator | CODE-GREEN | #326 (code + VM wiring shipped; external verify pending) |
| Is there an independent status page + uptime monitoring? | Better Stack account, monitors, footer link (+ Cloudflare Health Check per `docs/DEPLOYMENT.md`) | operator | OPEN — **deferred for the friend beta; REQUIRED again for a stranger audience (2026-08-13)** | #282. ⚠️ **Correction 2026-07-31:** the Jul 28→29 ~31h downtime was an **intentional owner poweroff to save cost**, NOT a silent failure. The earlier "gap PROVEN in production / beta-critical" framing was wrong and is retracted. Still true: `health-check.yml`'s schedule silently died 2026-06-17 and nobody noticed. **⚠️ Re-opened 2026-08-13:** the 2026-07-31 deferral was scoped to "owner + ~3 friends who will simply text him". #282's own re-open criteria are *"(a) users are people who won't contact you directly, (b) you start charging someone who isn't a friend, or (c) Stage B"* — a non-friend audience trips all three, so this is a **blocker for that audience** and only ever was deferred for the friend beta. Two constraints when building it: Bot Fight Mode is ON, so any external monitor must be verified against it (`docs/EDGE_SECURITY.md:44-46`), and it needs a documented maintenance-mute step so an intentional poweroff does not page falsely. Self-hosted Uptime Kuma was **rejected** — the status page must not die with the host it reports on. |
| Will we hear about cost blowouts (billing alert + LLM-cost rule)? | DO billing alert + one Grafana rule over `llm_cost_usd_total` after #326 activation | operator | OPEN | #291 (counter shipped); `docs/dashboards/llm-cost-panel.json` |
| Is unit-economics review in place (COGS runbook + R2 gauges)? | Eyeball the R2 Metrics tab after #326 activation | operator | CODE-GREEN | #292, #293 (price book fixed; gauges shipped) |

### Product honesty & UX

| Gate (question) | Action item | Owner | Status today | Evidence / signal |
|---|---|---|---|---|
| Does no surface promise virality; is every score estimate-framed? | None — structural test runs in every suite | build | GREEN | `tests/test_compliance_no_virality.py` + `tests/test_static.py` pins; FitBadge tiers (#192) |
| Is billing wired for the beta (minute packs, verified webhooks, reconciliation)? | None — a real purchase credited minutes through the webhook | build | **GREEN (2026-08-14)** | **Proven end to end by a real live purchase**, which is the only thing that could close this row. Prod log, one `request_id=48801afa…`: `billing checkout_session pack=starter` → `event=billing_webhook_received` → `billing grant creator=eb9af967… minutes=200 reason=purchase` → `event=billing_webhook_processed pack_id=starter`. **`billing_webhook_received` had never once appeared in this app's history before 2026-08-14.** The grant came through the webhook itself, *not* the `reconcile_stripe_ledger` fallback — which is exactly the distinction this row demanded. **It took four stacked defects to get here, each hidden by the one before it:** (1) Issue 453 — `HTTPXClient(allow_sync_methods=False)` made every sync Stripe call raise for 10 weeks, so no session was ever created; (2) Issue 485a — Cloudflare's **OWASP Core Ruleset** blocked Stripe's webhook POSTs at the edge (Stripe received a Cloudflare block page; Ray `a2acda78fc1e5509`), invisible until (1) was fixed and deliveries actually began; (3) Issue 485b — the endpoint registered in Stripe was `/webhooks/stripe` while the app serves `/billing/webhook`, a 404 that would have bitten the instant the edge block lifted; (4) Issue 486 — account branding, non-blocking, still open. Fixes: `RequestsClient`; the `stripe-webhook-skip-waf` managed-rules exception (`docs/EDGE_SECURITY.md` Rule 2); the endpoint URL corrected in place so the signing secret survived. *Standing lesson, now twice-proven on this row: the prior GREEN rested on `doctor.py --full`, which probed Stripe with a raw `httpx.get` and never exercised our client, and `POST /billing/checkout` returned a clean 200 while the customer received nothing. **A green intermediate layer is not a working feature.*** |

**Stage A totals:** 34 gates — **19 GREEN · 5 CODE-GREEN · 10 OPEN · 0 RED** (recounted from the
rows themselves 2026-08-14). **Changed 2026-08-14: billing went RED → GREEN — the last RED on the
board is cleared.** A real purchase credited 200 minutes through the webhook path after four
stacked defects were unwound (transport → Cloudflare OWASP edge block → wrong endpoint URL); see
that row for the evidence. Changed 2026-08-13 (second pass): **#395 was added as a row** — it was
a flagged BETA BLOCKER that had never appeared in this ledger at all, which is why the gate count
rose by one rather than a status changing. Changed 2026-08-13 (first pass): #284 flags-flip and
#290 spend-trip went CODE-GREEN → GREEN, and the W1/W2
staging-verify residuals row went OPEN → GREEN, all on staging-drills run 31727428785.
Earlier: 2026-08-12 billing flipped GREEN → RED on Issue 453, a 10-week total checkout
outage; 2026-08-11 #26 flipped GREEN with live evidence; 2026-07-29 #24 + #25. The honest
distance-to-beta number is **15 gates not fully green**, and the last hard blocker for
inviting the first friends is now **#28 (friend smoke)** alone — billing cleared 2026-08-14,
so #28 is finally worth running. Nothing else gates it.

⚠️ **Correction 2026-08-11.** This paragraph previously named #282 uptime monitoring as a
blocker "which the 2026-07-29 31-hour silent outage proved beta-critical." That contradicted
#282's own row, which was corrected 2026-07-31: the outage was an **intentional owner
poweroff to save cost**, not a silent failure, and the "beta-critical" framing is explicitly
retracted there. #282 is **deferred for the invite-only beta by owner call** and is not a
Stage-A blocker. The stale sentence has been removed rather than left to read as a third gate.

---

## Stage B — public launch (Issue #30)

Everything in Stage A, plus:

| Gate (question) | Action item | Owner | Status today | Evidence / signal |
|---|---|---|---|---|
| Has Google verified the OAuth app (Testing → In production)? | Submit the READ-ONLY scope set; keep the `youtube.upload` submission separate (#194-gated, needs the YouTube API compliance audit) | operator | OPEN | #29 (1–4 week external review) |
| Does the deployment hold under the beta load profile? | Run the four staging Locust scenarios; consume pass/fail here | staging-verify | OPEN | #261; `docs/assessment/REPORT.md` verdict condition ("fresh Locust run confirms axis A/B") |
| Is every migration proven reversible in CI? | None — verified in code 2026-08-13 | build | **GREEN (2026-08-13)** | #296. **This row read OPEN until 2026-08-13 and was stale.** Issue 296's own status is `DONE (2026-07-03, W3)` and the machinery is present and readable: `.github/workflows/ci.yml` `migration-lint` performs the ONLINE round-trip — `pg_dump --schema-only` at head → `alembic downgrade $DOWN` → `upgrade head` → second dump → byte-diff, failing the PR with *"Schema did not round-trip"* (`ci.yml:365-406`) — and `:408` "Detect no-op / irreversible downgrades (Issue 296)" runs `scripts/check_downgrades.py` against `alembic/DOWNGRADE_EXCEPTIONS` (stale allowlist entries also fail). Both of #296's ACs are therefore satisfied. Local dry-run round-tripped 34 revisions byte-identical. |
| Are SLOs defined with burn-rate alerts? | Define SLOs + first alerts (dropped from the beta scope per the #282 rescope) | build | OPEN | #236 |
| Has the key-rotation runbook been executed end-to-end? | Run `scripts/rotate_token_key.py` on staging; confirm tokens still decrypt | operator | OPEN | #30 AC; runbook written GREEN (`docs/RUNBOOKS.md`) |
| Does a final security review pass (no PII/token in logs; deletion tested on prod)? | Log sweep + prod `DELETE /auth/me` exercise + isolation confirm | operator | OPEN | #30 AC (deletion first exercised in #28) |
| Are `ALLOWED_ORIGINS` + `/docs` re-verified locked on prod at launch? | `curl /docs` → 404; container env shows the exact origin | operator | OPEN | #24 AC re-run at launch; env-gated in `main.py` |
| Is pricing settled beyond minute packs (plan tiers)? | Product decision — minute packs shipped; usage tiers remain unpriced | operator | OPEN | Issue 21 shipped the beta model; CLAUDE.md pricing note |
| Are all gates green, signed off, and v1.0.0 tagged? | Final sweep of this file + tag | operator | OPEN | #30 (blocked by #29, #303) |

**Parked (NOT Stage-B gates):** the 10k-scale track — GKE/KEDA (#275–280, #287),
PgBouncer/pool sizing (#58/#259, #262, #263) — descoped for v1 per DECISIONS 2026-06-26;
revisit only if growth outpaces the beta topology.

---

## Stage A→B execution plan — the master runthrough (added 2026-08-13)

**Read this first: there is no "Stage A½."** Inviting users who are *not* personal friends is
**Stage B**, not a widened Stage A. The reason is not the 100-user cap — it is that **in Testing
mode Google expires every tester's OAuth connection after 7 days** (`docs/ACCESS.md:51-54`), so each
user must re-click "Connect YouTube" weekly *and* be manually whitelisted first. No stranger
tolerates that. **Issue #29 (Google OAuth verification, 1–4 week external review) is therefore a
hard blocker for this audience**, and it is the only item on this page with a clock you cannot
compress. Start it on day 0 and let it bake while everything else proceeds.

This section is the ordered working checklist. Every row cites the runbook that owns the procedure —
it deliberately does **not** restate steps, so there is exactly one copy of each. Fill the evidence
blank as you go; a row is done only when its blank is filled. Statuses above stay the record.

### Track 1 — Start the external clock (day 0, ~2h, then unattended)

- [ ] **Confirm the OAuth publishing status in the console** and record it here: ________.
      *Why first:* the docs disagree. `docs/DECISIONS.md:12687` says "In production / 1-of-100";
      `docs/GO_LIVE.md:50` and `docs/ACCESS.md:45` (both live-verified 2026-08-11) say **Testing**.
      The DECISIONS mention is a parenthetical aside inside a *pricing* entry, so the live-verified
      sources very likely win — but every #29 sequencing decision depends on the real value, and
      Google caps *unverified* apps at 100 users in **either** mode, so the number cannot
      disambiguate it. Open Google Auth Platform → Audience and look.
- [ ] **Confirm ≥2 test users are configured** (the residual carried inside the GREEN #26 row at
      `:50` — never actually verified). If it is still only the owner, add someone before #28.
      Count: ________
- [ ] **Submit #29 with the READ-ONLY scope set only** — `openid`, `userinfo.email`,
      `userinfo.profile`, `youtube.readonly`, `yt-analytics.readonly` (`youtube/oauth.py:46-51`).
      **Do NOT include `youtube.upload`**: it drags in the heavier YouTube API compliance audit and
      can block or massively delay verification (#194 keeps it a separate, later submission).
      Per-scope written justification required. The public-homepage requirement is already satisfied
      (`main.py:index()` serves `static/landing.html`, Issue 376(a)). Submitted: ________
- [ ] Verify the app name reads **AutoClip** everywhere Google will look (app, ToS, privacy policy)
      — Google's review checks that consistency. Runbook drift fixed 2026-08-13.

### Track 2 — Clear the billing RED (~30 min; everything else is downstream)

- [x] **Buy the smallest minute pack on prod for real** — done 2026-08-13. Session creation and
      payment both succeeded (`cs_live_a119Bph…`, paid, $18.00, pack `starter`); Issue 453's outage
      is confirmed fixed.
- [x] **Fix the Cloudflare edge block (#485a)** — done 2026-08-14. The OWASP Core Ruleset was
      rejecting Stripe's POSTs before they reached the app. `stripe-webhook-skip-waf` managed-rules
      exception deployed; expression + rationale in `docs/EDGE_SECURITY.md` Rule 2.
- [x] **Fix the webhook URL (#485b)** — done 2026-08-14. Endpoint edited in place to
      `https://autoclip.studio/billing/webhook`; signing secret unchanged, so `STRIPE_WEBHOOK_SECRET`
      needed no update.
- [x] **A purchase credits minutes through the webhook itself** — done 2026-08-14, 200 minutes
      granted under `request_id=48801afa…`. **This row is GREEN.**
- [ ] **#486 — separate Stripe account for AutoClip** (owner decision 2026-08-13). Required before
      non-friends pay: Checkout currently shows another product's branding *and* a personal name
      (`branding_settings.display_name = "Reese Ludwick"`) on the card-entry page. Done: ________
      > ⚠️ Stripe transport stays `RequestsClient`. **Never** revert to `HTTPXClient` — two stacked
      > defects, 10-week total checkout outage (`billing/stripe_client.py`, DECISIONS 2026-08-12).

### Track 3 — The DR floor, before any stranger's data exists

Strictly ordered by consequence (`docs/runbooks/255-258-dr-durability.md:6`).

- [ ] **1. Secrets escrow (#255) — do this FIRST.** `TOKEN_ENCRYPTION_KEY`, `JWT_SECRET_KEY`, and a
      snapshot of `/opt/autoclip/.env` to **two independent legs** (password manager **and** GCP
      Secret Manager). → `docs/RUNBOOKS.md:578-589`. *Without it a perfect Postgres restore yields
      useless ciphertext — every user's OAuth tokens, unrecoverable. It is the prerequisite that
      makes every other backup worth having.* Never store these inside the backup they protect.
      Done: ________
- [ ] **2. Rotate the exposed Anthropic key.** → `docs/SECRETS.md:219-222`: new key in console →
      VM `.env` → `doctor.py --full` green → **then** revoke the old one. Done: ________
- [ ] **3. Nightly PG backups (#256/#257).** Create `creatorclip-backups`; set `BACKUP_R2_BUCKET` +
      `BACKUP_ENCRYPTION_KEY`; install cron `7 3 * * * cd /opt/autoclip && ./scripts/backup_pg.sh`.
      → `docs/RUNBOOKS.md:590-606`. Done: ________
- [ ] **4. Run the restore drill.** → `docs/RUNBOOKS.md:641-648`. **`python3
      scripts/reapply_erasures.py` afterwards is mandatory**, not optional — it is what keeps a
      restore from resurrecting data a user asked to be erased. RTO recorded: ________
- [ ] **5. R2 Object Lock (#258).** Compliance mode ≥14d — **not** Governance, which is
      admin-overridable and therefore not tamper-proof — plus per-prefix lifecycle.
      → `docs/RUNBOOKS.md:596-601`. Reconcile the windows against right-to-erasure (#254), which
      also closes that CODE-GREEN row. Done: ________
- [ ] **6. Redis durability (#288).** cron `27 3 * * * ./scripts/backup_redis.sh`; then the restart
      drill at `docs/RUNBOOKS.md:711-714`. *Staging Redis is intentionally ephemeral — do not "fix"
      it.* Done: ________
- [ ] **7. Cloudflare edge rate limit (#286).** `preauth-rate-limit`, 10 req/min per IP, Managed
      Challenge. → `docs/EDGE_SECURITY.md:26-79`. Keep `/health` **out** of the expression. The
      verify loop must show the challenge **and** `docker compose logs app` showing no request
      flood — that is what proves the block happened at the edge rather than in the app.
      Done: ________

### Track 4 — The fresh-upload verification session (the linchpin)

*One session, designed so a single upload clears the maximum number of pending acceptance criteria.
Nearly everything still unchecked across L26–L29 is of the form "upload one real video and check N
things at once."*

- [ ] **Pre-flight — do not skip:** `python3.12 scripts/r2_set_cors.py https://autoclip.studio`.
      The `ExposeHeaders ETag` is load-bearing: **without it multipart completes stall at 100%**
      (DECISIONS 2026-08-05). Run it before any drill or you will spend the session debugging a
      phantom. Then flip `OVERLAY_BAND_DETECT_ENABLED=true` and `CAMERA_REGION_DETECT_ENABLED=true`.
- [ ] **The three #395 upload drills** — see the #395 row above for why all three are listed
      here rather than just the one that has a checkbox in `issues.md`.
- [ ] **Clip audit on the output.** `scripts/clip_audit.py` (loudness + true peak — note `peak=true`
      was only added 2026-08-10, so every audit before that silently reported no peak data at all),
      plus frame-extraction spot-checks. Clears in one pass: **427** caption-on-face
      (`docs/issues.md:1944`), **430** camera region (`:1993`), **448** overlay band (`:2825`),
      **444** triage idempotency (`:2644`), **437** (`:2201`), **466** backfill drill (`:3510`),
      **467** worker-path render (`:3540`), **478** full-resolution re-freeze (`:3817`).
- [ ] **Then look at the clips as a creator, not an auditor.** Would you post these? *No gate on
      this page covers that judgment, and it is the actual product question.* The eval harness
      proves window **geometry** — it has never proven a clip is good. Verdict: ________

### Track 5 — One consolidated fix wave

*Sequencing decision (DECISIONS 2026-08-13): triage what Track 4 surfaces **together with** the
known-open defects, then fix once. Rationale — every previous live upload surfaced defects the
backlog had not predicted (first → Issues 427–430; second → 448, 449, 450), so fixing blind ahead of
the upload risks fixing the wrong things.*

- [ ] **Issue 484 — the meaning-inverting cold open.** The highest-impact known clip defect, and
      until 2026-08-13 it was open, unfiled and unowned. See `docs/issues.md` § 484.
- [ ] **Issue 441 residual** — hedge opens (`"Like,"`, `"maybe"`) and the fragment class survived
      Issue 449's fix (`docs/issues.md:2487`). Folded into 484.
- [ ] **Issue 450** — reframe landing on the wrong person. Issue 440's motion criteria passed on a
      shot of the wrong speaker, and *"this audit graded 440 green on the numbers alone and missed
      it"* (`docs/issues.md:2414`) — a standing warning about trusting numeric criteria over pixels.
- [ ] Every fix ships an eval fixture, ratcheting `SCENARIO_FLOOR` above 31.

### Track 6 — Issue 445, the three-pile triage UI

- [ ] **Build it** (owner call 2026-08-13). It is **genuinely unbuilt** — 6 unchecked ACs at
      `docs/issues.md:2672-2683` — despite an earlier handoff claiming the L27 triage UI had
      shipped. Strangers hit the review queue on their first upload, and today reviewed state does
      not survive a reload and the Dashboard badge counts the wrong thing
      (`pages/Dashboard.tsx:108`). Run a real CHECK phase first: four design questions are still
      open in the issue body (`docs/issues.md:2663-2670`).

### Track 7 — Gates the friend beta deferred that a stranger audience re-opens

- [ ] **#282 status page** — re-opened; see its row above for the criteria it now trips.
- [ ] **#326** — create the Grafana Cloud + Sentry projects and set the two GitHub secrets. Code and
      VM wiring already ship. This **unblocks #291**, which is otherwise hard-gated behind it.
- [ ] **#291 cost alerts** — DO billing alert + one Grafana rule over `llm_cost_usd_total`; the
      panel JSON already exists at `docs/dashboards/llm-cost-panel.json`.
- [ ] **#236 SLOs** — minimum viable for this audience: one 5xx-rate alert + one Celery-failure
      alert. The full SLO set remains Stage-B scope.
- [ ] **Key-rotation dry run** (#30 AC) — `scripts/rotate_token_key.py` on staging, keys passed via
      **env, never argv** (argv is visible in `ps` on the shared VM and persists in shell history).
      → `docs/RUNBOOKS.md:138-229`. Once strangers' tokens are in the DB an untested rotation path
      is a real liability. Done: ________
- [ ] **`MAILING_ADDRESS`** (#246) — now **required**, where it was optional for friends: lifecycle
      email to non-friends is commercial mail under CAN-SPAM. It prints in every footer and becomes
      public, so use a PO box or CMRA mailbox. Until set, all lifecycle email stays correctly
      **skipped** (`config.py:752`).
- [ ] **Final security review** (#30 AC) — log sweep for PII/token, prod `DELETE /auth/me` exercise,
      per-creator isolation confirm, `curl /docs` → 404, `ALLOWED_ORIGINS` re-verified (#24 re-run).
- [ ] **Sign off** the Stage B row in the table at the bottom of this file.

**Stage B totals:** 9 additional gates — **1 GREEN · 8 OPEN** (#296 corrected from a stale OPEN to
GREEN on 2026-08-13 — the CI machinery was already shipped and readable; see its row).

---

## T-minus day plan (each stage's go-live)

- **T-3 — feature freeze.** Only gate-closing fixes merge. Full suite + Layer-0 + eval
  green on `main`; staging gate (#298) exercised on the release candidate.
- **T-2 — verification day.** Work the OPEN operator rows above top-to-bottom; record
  each closure in this file with date + evidence.
- **T-1 — final review & sign-off.** Walk this scorecard end to end; any non-GREEN row is
  a NO-GO or an explicitly signed exception. Sign below.
- **T-0 — launch execution.** Deploy via `deploy.yml` (staging gate → prod → smoke);
  solo-responder "war room" = cleared calendar + alert channels open
  (`docs/INCIDENT_RESPONSE.md` escalation model); monitor logs/Grafana/Sentry actively.
- **T+1 — stabilization.** 48h monitoring window (#28 pattern): log triage, cost check
  against the #290 thresholds, no-new-SEV1 confirmation before widening invites.

## Abort / rollback criterion

**Abort the launch when either fires:**

1. **Deploy-time:** the post-deploy smoke fails — the pipeline auto-rolls-back by
   re-tagging the previous digest as `:rollback` and relaunching with
   `IMAGE_TAG=rollback` (#271, fixed by #298 — see `docs/DEPLOYMENT.md`
   "Auto-Rollback on Failed Smoke Test"). The run still reports failed; do not re-attempt
   until the cause is root-caused.
2. **Run-time:** any SEV1 per the `docs/INCIDENT_RESPONSE.md` severity ladder during the
   T+1 window (data loss/corruption, security or privacy breach, full outage). Response:
   flip the relevant kill switch (#284) / spend trip (#290), roll back per the
   `docs/RUNBOOKS.md` migration-rollback runbook if schema-coupled, pause invites, and
   run the incident loop before any retry.

Schema recovery is **roll-forward-first** (expand/contract, #270); `alembic downgrade`
is break-glass only.

## Deferred acceptance criterion (approved)

The #303 AC "a dry-run of the full checklist passes before Issue 30 is attempted" is
**deferred to the Issue-30 runway** (approved 2026-07-02): the Stage-B dry-run happens
after Stage A completes, immediately before #30 execution. Stage-A rows are dry-run
implicitly by executing the #24→#25→#26→#28 chain.

## Sign-off

| Stage | Decision (GO / NO-GO) | Owner | Date |
|---|---|---|---|
| Stage A — private beta | _pending_ | Reese | _____ |
| Stage B — public launch (#30) | _pending_ | Reese | _____ |

*Last reconciled: 2026-08-13 — added the missing #395 row, corrected the stale #296 OPEN to GREEN,
re-opened #282 for a non-friend audience, and added the "Stage A→B execution plan" section. Rows
were also updated 2026-08-11, -12 and -13 (the previous "2026-07-02" line predated all of those and
understated how current the ledger was). Update a row's status only with evidence, and date the
change.*
