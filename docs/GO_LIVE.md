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
| Are prod secrets provisioned, unique, and off-git? | Run the #24 verification pass on the VM (`.env` fields, key uniqueness, GH Actions secrets, `workflow_dispatch` deploy green) | operator | OPEN | #24 ACs; `scripts/doctor.py` preflight in `deploy.yml` |
| Is per-creator tenant isolation structurally enforced in prod? | None — RLS role split active | build | GREEN | #343 (verified live 2026-06-30); `docs/DEPLOYMENT.md` "RLS one-time setup" |
| Has the exposed Anthropic key been rotated? | Rotate in the provider console + VM `.env` | operator | OPEN | `LEFT_OFF.md` operator checklist |
| Is edge rate limiting live on `/auth/*`? | Apply the Cloudflare rule + run the 429 verify loop | operator | OPEN | #286 (config committed); `docs/EDGE_SECURITY.md` |
| Are per-creator app rate limits + pre-job quotas live? | Live/staging 429 smoke via `scripts/live_smoke.py` | operator | CODE-GREEN | #228 (shipped 2026-06-24; residual smoke), #312 (async storage), #321 (brief quota) |
| Are LLM spend caps + the cost circuit breaker armed? | Staging trip drill ($ thresholds → `llm_generation` flag) | operator | CODE-GREEN | #290; `billing/spend_guard.py`; `docs/RUNBOOKS.md` trip/reset section |
| Can risky subsystems be killed without a deploy? | Staging flip-disables-live-subsystem proof | staging-verify | CODE-GREEN | #284; `flags.py`, `scripts/flags.py` |
| Are `TOKEN_ENCRYPTION_KEY` / `JWT_SECRET_KEY` / `.env` escrowed off-box? | Copy the 3 secrets to 1Password + GCP Secret Manager | operator | OPEN | #255 (runbooks/docs done); `docs/SECRETS.md` |

### Compliance & Privacy

| Gate (question) | Action item | Owner | Status today | Evidence / signal |
|---|---|---|---|---|
| Is YouTube data-retention/refresh ToS-compliant? | None | build | GREEN | Issue 75b (30-day partial-staleness purge); `docs/COMPLIANCE.md` |
| Are ToS + Privacy Policy live, linked, and accurate? | Confirm the deployed pages during #28 | build | GREEN | Issue 14; #252 (GDPR/CCPA rewrite); footer links (Wave-6 Fix B) |
| Does account deletion revoke tokens + purge media/data? | Prod exercise happens inside #28 | build | GREEN | #158 (+#247/#248/#249); verified in-repo 2026-07-02: `routers/auth.py` erasure helper incl. Google `/revoke` POST |
| Is the `yt-dlp` path guarded (off by default, own-content only)? | None | build | GREEN | Verified in-repo 2026-07-02: `youtube/ingest.py:89` gate on `config.py:421` `YTDLP_ENABLED=False` |
| Do we request only necessary OAuth scopes? | Keep the login set read-only; `youtube.upload` stays incremental-consent only | build | GREEN | Verified in-repo 2026-07-02: `youtube/oauth.py` `SCOPES` (read-only) + separate `PUBLISH_SCOPE` (#194) |
| Is the OAuth consent screen configured with beta test users? | Google Cloud Console: Testing status, scopes byte-identical to code, ≥2 test users | operator | OPEN | #26 |
| Is the regulatory posture shipped (COPPA age gate, accessibility statement, GPC)? | None | build | GREEN | #300, #301, #302 |
| Do restores honor prior erasures (backup-erasure stance)? | Confirm R2 lifecycle/Object-Lock numbers in the dashboard | operator | CODE-GREEN | #254; `scripts/reapply_erasures.py`; `docs/RUNBOOKS.md` DR steps |

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
| Does every prod deploy pass a data-bearing staging gate first? | First VM run: tear down the old `cc139` project (or one `skip_staging` dispatch) | operator | CODE-GREEN | #298 (+#271 rollback fix); `docs/DEPLOYMENT.md` "Staging-Parity Gate" |
| Is migration safety enforced (Squawk, timeouts, expand/contract, rollback runbook)? | None | build | GREEN | #270, #294; `docs/MIGRATIONS.md` |
| Do deploys verify the critical journey and tag every promotion? | None | build | GREEN | #295, #297 |
| Are all external APIs provisioned with `/health` green? | Run `scripts/doctor.py --full` on the VM; Deepgram/R2 round-trips; no key in logs | operator | OPEN | #25 |
| Are the W1/W2 staging-verify residuals exercised? | Run the queued staging checks once the staging gate is live (not individually beta-blocking) | staging-verify | OPEN | "Remaining (staging)" lines on #190/#192/#198/#200/#201/#202/#245/#284/#290 in `docs/issues.md` |
| Has the full pipeline run end-to-end on prod with real friends for 48h? | Execute the #28 beta smoke + friend onboarding — the Stage-A capstone | operator | OPEN | #28 (blocked by #24/#25/#26) |

### Observability & Cost

| Gate (question) | Action item | Owner | Status today | Evidence / signal |
|---|---|---|---|---|
| Are logs/metrics/traces + error tracking live? | Verify Grafana Cloud + Sentry ingest on the live SaaS side | operator | CODE-GREEN | #326 (code + VM wiring shipped; external verify pending) |
| Is there an independent status page + uptime monitoring? | Better Stack account, monitors, footer link (+ Cloudflare Health Check per `docs/DEPLOYMENT.md`) | operator | OPEN | #282 (re-scoped for beta) |
| Will we hear about cost blowouts (billing alert + LLM-cost rule)? | DO billing alert + one Grafana rule over `llm_cost_usd_total` after #326 activation | operator | OPEN | #291 (counter shipped); `docs/dashboards/llm-cost-panel.json` |
| Is unit-economics review in place (COGS runbook + R2 gauges)? | Eyeball the R2 Metrics tab after #326 activation | operator | CODE-GREEN | #292, #293 (price book fixed; gauges shipped) |

### Product honesty & UX

| Gate (question) | Action item | Owner | Status today | Evidence / signal |
|---|---|---|---|---|
| Does no surface promise virality; is every score estimate-framed? | None — structural test runs in every suite | build | GREEN | `tests/test_compliance_no_virality.py` + `tests/test_static.py` pins; FitBadge tiers (#192) |
| Is billing wired for the beta (minute packs, verified webhooks, reconciliation)? | Confirm Stripe LIVE keys during #25 | build | GREEN | Issue 21; #205, #206; spend guard #290 |

**Stage A totals:** 32 gates — **12 GREEN · 7 CODE-GREEN · 13 OPEN**.
The honest distance-to-beta number is **20 gates not fully green** (13 OPEN + 7
CODE-GREEN verification residuals); the hard blockers for inviting the first friends are
the #24 → #25 → #26 → #28 chain.

---

## Stage B — public launch (Issue #30)

Everything in Stage A, plus:

| Gate (question) | Action item | Owner | Status today | Evidence / signal |
|---|---|---|---|---|
| Has Google verified the OAuth app (Testing → In production)? | Submit the READ-ONLY scope set; keep the `youtube.upload` submission separate (#194-gated, needs the YouTube API compliance audit) | operator | OPEN | #29 (1–4 week external review) |
| Does the deployment hold under the beta load profile? | Run the four staging Locust scenarios; consume pass/fail here | staging-verify | OPEN | #261; `docs/assessment/REPORT.md` verdict condition ("fresh Locust run confirms axis A/B") |
| Is every migration proven reversible in CI? | Build the downgrade CI check | build | OPEN | #296 |
| Are SLOs defined with burn-rate alerts? | Define SLOs + first alerts (dropped from the beta scope per the #282 rescope) | build | OPEN | #236 |
| Has the key-rotation runbook been executed end-to-end? | Run `scripts/rotate_token_key.py` on staging; confirm tokens still decrypt | operator | OPEN | #30 AC; runbook written GREEN (`docs/RUNBOOKS.md`) |
| Does a final security review pass (no PII/token in logs; deletion tested on prod)? | Log sweep + prod `DELETE /auth/me` exercise + isolation confirm | operator | OPEN | #30 AC (deletion first exercised in #28) |
| Are `ALLOWED_ORIGINS` + `/docs` re-verified locked on prod at launch? | `curl /docs` → 404; container env shows the exact origin | operator | OPEN | #24 AC re-run at launch; env-gated in `main.py` |
| Is pricing settled beyond minute packs (plan tiers)? | Product decision — minute packs shipped; usage tiers remain unpriced | operator | OPEN | Issue 21 shipped the beta model; CLAUDE.md pricing note |
| Are all gates green, signed off, and v1.0.0 tagged? | Final sweep of this file + tag | operator | OPEN | #30 (blocked by #29, #303) |

**Parked (NOT Stage-B gates):** the 10k-scale track — GKE/KEDA (#275–280, #287),
PgBouncer/pool sizing (#58/#259, #262, #263) — descoped for v1 per DECISIONS 2026-06-26;
revisit only if growth outpaces the beta topology.

**Stage B totals:** 9 additional gates — **0 GREEN · 9 OPEN**.

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

*Last reconciled: 2026-07-02 (Issue #303). Update a row's status only with evidence, and
date the change.*
