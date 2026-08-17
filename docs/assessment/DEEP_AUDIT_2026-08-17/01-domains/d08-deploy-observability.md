# D08 — Deployment, infrastructure, observability

**Domain researcher pass, 2026-08-17.** Read-only. Builds on
`00-groundtruth/process-map.md` §5–6 and `architecture-map.md` §A1/D4; does not re-derive them.

---

## Verdict

The **deploy pipeline is above standard for this scale** — the data-bearing staging gate, the
`PREV_IMAGE` digest capture, and a rollback that still exits non-zero are things most funded teams
don't have. The **observability story is inverted**: three telemetry rails (Prometheus, Sentry,
OTel) are code-complete and *none of them reach a consumer*, while the alerting layer — the only
part a solo responder actually needs — does not exist at all. The single VM is the right call and
I would not move it; but the topology is being asked to excuse three things it does not excuse:
**backups that are 0% armed, an uptime probe that probably cannot exist on the current Cloudflare
plan, and no browser-reachable staging URL.**

The one-line summary of the domain: **the project built twelve metrics and zero alerts.** For a
solo operator at 100 users, one external probe and one backup heartbeat are worth more than the
entire instrumentation layer that has been shipped.

---

## What the current (2026) standard is, with sources

**Alerting.** Symptom-based, tied to user impact, and ruthlessly pruned. The operative 2026 test is
*"if the on-call engineer cannot take a specific action to resolve it, the alert should not exist"*
([incident.io, SRE alerting best practices](https://incident.io/blog/sre-alerting-best-practices);
[Golden Signals guide, June 2026](https://autoheal.ai/learn/sre-golden-signals-guide)). The four
golden signals are explicitly framed as *"the minimum viable view of user-facing service health"* —
a floor, not a target. Alerting ≠ paging: self-correcting and awareness-class events should route
to a digest, not a phone
([OpenGov monitoring/alerting blueprint](https://opengov.com/article/a-monitoring-alerting-and-notification-blueprint-for-saas-applications/)).

**External uptime probing.** A status/uptime monitor must not share a failure domain with the thing
it reports on. Free/cheap 2026 options: Better Stack free (10 monitors, 3-min interval, 3-day
retention), UptimeRobot free (50 monitors, 5-min — **but personal/non-commercial only since Oct
2024**, so a paid product needs Solo ≈ $7–9/mo), healthchecks.io free (20 cron/heartbeat checks)
([UptimeRobot comparison, 2026](https://uptimerobot.com/knowledge-hub/monitoring/11-best-uptime-monitoring-tools-compared/);
[silent-failure tool comparison 2026](https://www.notilens.com/blog/best-silent-failure-monitoring-tools)).

**RTO/RPO.** Targets come from business impact, not a chart; the documented 2026 anti-pattern is
over-buying (*"startups spend 40% of their infrastructure budget achieving sub-minute RPO for
internal dashboards that could safely tolerate daily restores"*). Recovery testing quarterly
minimum ([RPO/RTO practical targets 2026](https://khimananda.com/blog/rpo-and-rto-explained);
[oneuptime RPO/RTO](https://oneuptime.com/blog/post/2026-02-06-rpo-rto-targets-observability-opentelemetry/view)).
This project's recorded position — logical `pg_dump`, 24 h RPO, PITR only at larger scale
(`docs/DECISIONS.md:2334-2335`) — **is exactly the current standard.** The defect is activation, not design.

**Zero-downtime on one box.** 2026 consensus for small deployments: you need three things — a
health endpoint that gates traffic, connection draining, and an atomic route switch. Kamal
(Traefik + container labels) and the `docker-ztd` Compose plugin are the named lightweight
implementations; *"for very small apps under 50 requests per second, a simple rolling update behind
a health-checked proxy is often sufficient and easier to maintain"*
([Temps, zero-downtime Docker 2026](https://temps.sh/blog/how-to-add-zero-downtime-deployments-docker);
[oneuptime, updating containers without downtime, Jan 2026](https://oneuptime.com/blog/post/2026-01-06-docker-update-without-downtime/view)).

**Continuous deployment without a human gate.** Elite DORA performers deploy on demand with change
failure rate ~5% and recover in under an hour; trunk-based + automated rollback is the shape, not
manual approval ([DORA metrics 2026](https://larridin.com/developer-productivity-hub/dora-metrics-explained-complete-guide-2026)).
**The missing human approval step in `deploy.yml` is not a finding.** It is current standard given
that a real pre-prod gate and an automated rollback both exist.

**Hosting economics.** DO 4 vCPU / 8 GiB ≈ **$48/mo** flat; Fly shared-CPU-4x ≈ **$42.79/mo** but
usage-billed and less predictable; DO/Render flat pricing is the recommended choice when cost
predictability beats global distribution
([getdeploying DO vs Fly](https://getdeploying.com/digitalocean-vs-flyio);
[DigitalOcean, Render alternatives 2026](https://www.digitalocean.com/resources/articles/render-alternatives)).

**Docker log rotation.** `json-file` `max-size` **defaults to `-1` (unlimited)**; rotation is off
until you set `max-size`/`max-file`
([Docker Engine json-file driver docs](https://docs.docker.com/engine/logging/drivers/json-file/)).

**Cloudflare feature availability.** Standalone **Health Checks: Free = No** — Pro/Business/
Enterprise only ([Cloudflare Health Checks docs](https://developers.cloudflare.com/health-checks/)).
Cloudflare **Access free tier = 50 seats**, full ZTNA on self-hosted apps
([Cloudflare Zero Trust free-plan limits 2026](https://zerometric.net/research/cloudflare-zero-trust-free-plan-limits-2026/)).

---

## Findings

### F1 — The only continuous production signal is a Cloudflare feature that is not on the Free plan (HIGH)

`docs/DEPLOYMENT.md:145-172` designates **Cloudflare Health Checks** as the owner of continuous
uptime monitoring, and `.github/workflows/health-check.yml:3-12` deletes the GitHub cron *because*
of that designation. `docs/EDGE_SECURITY.md:87` states, in the present tense about this zone, *"On
the Free plan the only lever that exempts traffic from it is an IP Access Rule"*, and
`docs/DECISIONS.md:12290-12294` ships a rate-limit design sized to *"Cloudflare edge rate limiting
on the FREE tier (1 rule)"*. **Cloudflare's own availability table lists standalone Health Checks
as `Free | No`.**

So the replacement for the monitor that was removed may never have been creatable, and there is
**zero evidence anywhere in the repo that it exists** — no health-check ID, no notification
destination, no `evidence: ____` row (every other operator step in `GO_LIVE.md` gets one).

*Failure scenario:* the droplet is powered off or the tunnel deregisters at 02:00 on a Saturday.
Cloudflare serves 530/1033. No probe fires, because the probe was never provisionable on this plan.
The outage is found when the owner next opens the site. **This has already happened, for up to 9
days** (`docs/OFF_COURSE_BUGS.md:104`) — and the post-mortem for it blamed the *removed* GitHub
cron without ever asking whether the replacement had been created.

*Verification (60 seconds, cannot be answered from the repo):*
```
curl -s -H "Authorization: Bearer $CF_API_TOKEN" \
  "https://api.cloudflare.com/client/v4/accounts/$CF_ACCOUNT_ID/healthchecks" | jq '.result|length, .errors'
```
An empty `result` or a `1015`/plan error confirms it. Also check the zone plan:
`GET /zones/$ZONE_ID | jq .result.plan.name`.

*Judgement call?* No — the plan-availability fact is documented by Cloudflare; the only uncertainty
is whether the zone was silently upgraded to Pro, which the repo does not record either way.

---

### F2 — Every business metric the project built is exported nowhere, and two runbooks depend on reading them (HIGH)

Three separate facts compose into a dead rail:

1. `/metrics` **auto-disables in production** when `METRICS_TOKEN` is unset (`config.py:1142-1150`),
   and it is unset (`docs/OFF_COURSE_BUGS.md:128`).
2. Nothing on the VM scrapes it anyway — no Prometheus, no Grafana Alloy, no agent in
   `docker-compose.prod.yml`.
3. OTel **deliberately does not bridge** the prometheus-client registry:
   `observability.py:786-788` — *"Push path only — prometheus-client stays for local /metrics
   scraping. Do NOT create a second prometheus-client bridge here."*

Consequence: `llm_cost_usd_total`, `celery_queue_depth`, `render_failures_total`,
`beat_lock_skips_total`, `r2_bytes_stored`, `db_pool_checked_out` — all 12 — increment in-process
and are read by nothing, **even if Grafana Cloud is fully live.** OTel exports auto-instrumentation
spans/metrics, not these counters.

*Failure scenario A:* `docs/RUNBOOKS.md:755-772` ("Monthly Cost Review") instructs the operator to
open `docs/dashboards/llm-cost-panel.json` (`sum by (provider) (increase(llm_cost_usd_total[1d]))`)
and to cross-check `r2_bytes_stored{prefix}` to confirm the ToS source-media purge is running. Both
queries return no series. The runbook is unexecutable as written, and the check it exists to
perform — *is the 72 h retention purge actually running* — silently never happens.

*Failure scenario B:* `GO_LIVE.md:84` "Will we hear about cost blowouts?" prescribes *"one Grafana
rule over `llm_cost_usd_total`"*. That rule cannot be built. A prompt-loop regression that 10×'s
Anthropic spend is invisible until the spend guard trips at its own hard cap or the invoice arrives.

*Cheapest fix, and it is not "stand up Prometheus":* the spend guard and the Beat sweeps already run
and already have a notification rail (`notify/`, Resend). Emit the alert from the code that already
computes the number, into the mailer that already exists. That is a smaller change than wiring a
scrape target, and it works today.

---

### F3 — `doctor.py` probes every paid dependency and none of the four "silence" variables (HIGH)

`scripts/doctor.py` (526 lines, the deploy preflight) live-probes Postgres, Redis, Anthropic,
Voyage, Deepgram, R2 and Stripe. `grep -n "SENTRY\|OTEL\|METRICS_TOKEN\|BACKUP_R2" scripts/doctor.py`
returns **nothing**. And both initialisers are *silent* no-ops — `observability.py:654` (`if not
dsn: return`) and `observability.py:748-750` (`if not endpoint: return  # strict no-op`) log no
line on the disabled path.

So there is no layer — not config validation, not the preflight, not the boot log, not `/health`
(`main.py:554-567` returns postgres/redis/storage/version only) — at which "observability
configured" is distinguishable from "observability dormant."

*Failure scenario:* an unhandled exception starts firing in `render_video_clips` for every creator.
Sentry is dormant because the GitHub secret was never set; nothing captured it; the deploy preflight
reported green on all 30 checks the day before. This is **structurally identical to
`OFF_COURSE_BUGS.md:148`** — the doctor green-lighting Stripe with a raw `httpx.get` while every
real call raised for 10 weeks. The lesson was recorded and applied to Stripe; it was not applied to
the observability surface, which is the one place where a silent failure is the *product*.

*Answer to "are Sentry and OTel live?" — it cannot be determined from this repository.* The exact
commands that determine it:
```
gh secret list -R reese8272/creatorclip | grep -E 'SENTRY_DSN|OTEL_EXPORTER'
ssh creatorclip-vm "grep -c '^SENTRY_DSN=.\+' /opt/autoclip/.env; grep -c '^OTEL_EXPORTER_OTLP_ENDPOINT=.\+' /opt/autoclip/.env"
ssh creatorclip-vm "docker compose -f /opt/autoclip/docker-compose.prod.yml exec -T app python -c \
  'import sentry_sdk; print(bool(sentry_sdk.Hub.current.client))'"
```
*Fix (structural, ~20 lines):* add all four to `doctor.py` as **warn-in-prod** checks, and make
`init_sentry`/`init_otel` log one INFO line on both paths ("sentry: enabled env=production" /
"sentry: DISABLED — SENTRY_DSN unset"). One grep of the boot log then answers the question forever.

---

### F4 — The prod critical-journey smoke has a silent-skip branch that the staging gate proves is unnecessary (MEDIUM-HIGH)

`deploy.yml:373-377`:
```
if [ -z "${CC_JWT_SECRET:-}" ]; then
  echo "WARNING: CC_JWT_SECRET not set — skipping critical journey smoke."
  ...
else  # run llm_harness --flow core
```
If that GitHub secret is unset, the deploy's Phase-2 verification does not run, the step exits 0,
and the deploy reports **green on a bare `/health` 200** — which is exactly the signal that
`GO_LIVE.md` has now twice recorded as insufficient ("a green intermediate layer is not a working
feature").

The **staging gate 240 lines above has no such branch** (`deploy.yml:130-133`): it runs
`sh -c 'CC_JWT_SECRET="$JWT_SECRET_KEY" python scripts/llm_harness.py --flow core'` — reading the
secret from *inside the container it just started*. The prod path can do the identical thing.

*Failure scenario:* the secret is rotated or the repo is re-created; `CC_JWT_SECRET` is empty; six
weeks of deploys report "Critical journey smoke" green having never executed it. Because
`deploy.yml` is a required-status-free path and nothing in `tests/test_ci_config.py` (22 tests,
none referencing `CC_JWT_SECRET`) asserts the branch is unreachable, the degradation is invisible.

*Fix:* delete the branch, mirror the staging invocation, and add one `test_ci_config.py` assertion
that the prod smoke step contains no `skipping` string. ~10 lines total.

---

### F5 — The auto-rollback takes down the tunnel and the database to swap an application image (MEDIUM)

`deploy.yml:332-333` and the mirrored `scripts/deploy.sh:84-85`:
```
docker compose -f docker-compose.prod.yml down --timeout 30 || true
IMAGE_TAG=rollback docker compose -f docker-compose.prod.yml up -d || true
```
`down` is **unscoped**. It destroys `cloudflared`, `postgres`, `redis`, `autoheal` and all three
worker services to roll back the `app` image.

*Failure scenario:* a bad image ships → the smoke fails → rollback runs → the `cloudflared`
container is destroyed and recreated, so during the down window plus tunnel re-registration
`autoclip.studio` returns **HTTP 530 / error 1033 ("origin unregistered from tunnel")** — the exact
signature the team burned days diagnosing in `OFF_COURSE_BUGS.md:104` and
`ISSUES_LOG ISSUE-2026-07-03-01`. Simultaneously `postgres` is stopped under any in-flight Celery
task, and if the `up -d` then fails for any reason (`|| true` swallows it) the **entire stack,
database included, stays down** while the job reports a single `exit 1` that reads as "deploy
failed", not "site is dark."

*Fix, one line:*
```
IMAGE_TAG=rollback docker compose -f docker-compose.prod.yml up -d --force-recreate --no-deps \
  app worker render-worker beat
```
Rollback then never touches the tunnel, the DB, or the broker, and the blast radius matches the
change being reverted.

*Related trap worth a comment:* after a rollback the stack runs `:rollback`. Any subsequent manual
`docker compose up -d` on the VM — without `IMAGE_TAG` set — silently rolls **forward** to the
broken `:latest`, because `${IMAGE_TAG:-latest}` defaults back.

---

### F6 — No Docker log rotation anywhere; a full disk is a total outage on a single-VM failure domain (MEDIUM)

`grep -n "logging:\|max-size\|max-file" docker-compose*.yml` returns nothing, and no
`daemon.json` is tracked. Docker's `json-file` driver **defaults `max-size` to `-1`, unlimited**
([Docker docs](https://docs.docker.com/engine/logging/drivers/json-file/)) — rotation is opt-in.

Eight always-on containers, one of them a Celery worker at `--loglevel=info` running ~40 task types
plus per-request JSON access logs from two uvicorn workers.

*Failure scenario:* `/var/lib/docker/containers/*/*-json.log` fills the droplet volume over some
months. Postgres cannot write WAL and refuses writes; every upload, every render and every billing
grant fails; `/health` reports `postgres: error` so the app healthcheck fails and `autoheal`
restart-loops the app forever. Recovery requires SSH — which, in the one whole-droplet incident
already on record, timed out with 100% ping loss. **A five-minute config change prevents the single
most plausible way this VM dies on its own.**

*Fix:* a `logging: {driver: json-file, options: {max-size: "20m", max-file: "3"}}` block per
service (or `"log-driver"`/`"log-opts"` defaults in `/etc/docker/daemon.json` plus a DO disk-usage
alert, which is free in the DO console).

---

### F7 — DR is designed to standard and 0% armed; RTO is a literal blank (HIGH)

The design is genuinely good: `docs/DECISIONS.md:2327-2390` (logical `pg_dump` at 24 h RPO,
`openssl enc -pass env:` with the argv-leak reasoning, separate bucket, Object Lock **Compliance**
not Governance, retention by R2 lifecycle so a script bug cannot mass-delete, key escrow on two
independent legs, the circular-dependency constraint spelled out). `scripts/backup_pg.sh` is a
careful piece of work. `docs/RUNBOOKS.md:572-670` walks all four loss modes and mandates
`reapply_erasures.py` after any restore — a GDPR subtlety most teams miss.

**None of it is on.** `BACKUP_R2_BUCKET` has never been set (`OFF_COURSE_BUGS.md:26`). That single
variable gates *both* the pre-migration dump (`deploy.yml:293-300`, which warns and continues) *and*
the nightly cron — `backup_pg.sh:66-69` hard-dies on it. The known off-course row frames this as
"migrations run without a safety dump." **The unstated consequence is larger: there are no backups
of any kind.** Not degraded RPO — no recovery point at all.

Compounding: `BACKUP_HEALTHCHECK_URL`, the dead-man's-switch that would tell you the nightly cron
stopped, is **not in `.env.example`** (it appears only in the script header and the runbook), so the
config SSOT does not prompt anyone to set it. And `docs/RUNBOOKS.md:648` still reads:
> measured **RTO** recorded here: ________

**Walk the recovery, today, if the droplet is lost right now:**

| Step | Time | Reality |
|---|---|---|
| Detect | **hours to days** | F1 — probably no external probe |
| Provision a new droplet, install Docker + awscli | 20–30 min | fine |
| Restore `/opt/autoclip/.env` | 5 min *or* **unrecoverable** | escrow is an operator step with no evidence row |
| Restore Postgres | **∞** | **no dump exists** |
| Re-point the Cloudflare tunnel | 10 min | dashboard click-ops |
| Re-apply erasures | 5 min | script exists and is idempotent |

**Actual RTO ≈ 1 h for a schema-only stack. Actual RPO = total loss** of the billing ledgers,
`preference_models` (the trained taste — irreplaceable, it is the product), `creator_dna`,
`clip_outcomes` and the consent records. R2 media survives; the database that indexes it does not.

*What the targets should be at this scale:* **RPO 24 h, RTO 4 h**, quarterly drill — which is
precisely what `DECISIONS.md:2334` already decided. The gap is one env var, one `aws configure`, one
cron line and one recorded drill. This is the highest consequence-per-hour item in the domain and
it has sat open since 2026-08-04.

---

### F8 — `render.yaml` is not dead config; it is armed config (MEDIUM)

`render.yaml` carries `autoDeployTrigger: commit` on **all three** services (lines 125, 153, 181)
and sets `ENV=production` with `VERBOSE_LOGGING=true` + `VERBOSE_LOGGING_ALLOW_PROD=true`
(lines 40–43) and `LOG_DIR=""`. A Render account for this repo demonstrably exists — a Render
Postgres was provisioned and is recorded as empty/never cut over (`DECISIONS.md:2395`).

*Failure scenario:* if the Blueprint is still linked to `main` — which nothing in the repo confirms
or denies — then **every push to main deploys a second `ENV=production` copy of the application**
that streams raw prompts, raw LLM responses, full transcripts and full request bodies (including
PII) into Render's log aggregator through a deliberately non-scrubbing formatter, bypassing
`redact.py`. Secrets are `sync: false`, so the blast radius depends on which were entered once —
and `R2_BUCKET`/`STRIPE_SECRET_KEY` are among them.

Even if unlinked, this file has **already misled the project once**: Issue 326's own brief was
written against `render.yaml` and had to be re-scoped mid-issue when someone noticed the live app
doesn't run on Render (`DECISIONS.md:2395`, recorded verbatim). `AUDIT_KNOWN_ISSUES.md` §E2 flags
the PII flags but explicitly classifies it as *"an operator check, not a finding against the code."*
**The `autoDeployTrigger` lines change that assessment** — it is not an inert document, it is a
deployment trigger sitting in the trunk of a repo with push-to-deploy.

*Fix:* `git rm render.yaml` and `deploy/charts/`, tag the commit `parked/render-blueprint` and
`parked/helm-chart`, note both in `docs/DECISIONS.md`, and confirm in the Render dashboard that no
Blueprint is linked. Nothing is lost — git tags are the archive.

---

### F9 — The staging gate is the best thing in the pipeline and no human can look at it (MEDIUM, judgement call)

`deploy.yml:146-147` deliberately `stop`s the staging app/worker after the gate, keeping only the
volume. `docs/STAGING_ACCESS.md:114` shows the only access path:
`ssh creatorclip-vm 'curl -s http://localhost:8001/health'`. There is **no browser-reachable
staging URL**, so `docs/DEPLOYMENT.md:121-141` "Gate 2 — Manual smoke test", a six-item human
checklist, has nowhere to be performed except production.

This matters more than usual here because the empirical record says so: `snag-taxonomy.md` §B shows
**CI catches <1 in 10 defects and caught zero SEV1s**, while the classes that do escape — Class 5
honesty/UX inversion (a failure string rendered in success green), Class 10 clip-engine quality —
are precisely the classes only a human eye on a rendered page catches. The pipeline has automated
everything that automation is bad at here and left the one thing humans are good at unreachable.

*Fix, $0:* add a second ingress rule in the existing Zero Trust dashboard —
`staging.autoclip.studio → <ccstage app>:8000` on the tunnel that already runs — and gate it with a
**Cloudflare Access** policy (free tier, 50 seats, full ZTNA on self-hosted apps). Change
`deploy.yml:146` from `stop app worker` to `stop worker` so the staging web process survives.
Cost: ~1 GB of RAM on the droplet, no new bill, no new vendor.

*Judgement call?* Yes — reasonable people can argue that at 100 users with an owner who tests in
prod, this is optional. I think the defect history argues otherwise.

---

## Answering the direct questions

**1 — Is the single VM the right call?** Yes, and I would not revisit it. Cost the alternatives
honestly: the current droplet is ≈ **$48/mo** flat (DO 4 vCPU/8 GiB) plus R2 and a free Cloudflare
plan. Render equivalent = web + worker + beat instances + managed PG + Key Value ≈ **$110–130/mo**,
on shared CPU that is worse for ffmpeg/MediaPipe, with an ephemeral filesystem hostile to the
render pipeline's `/tmp` usage. Fly shared-CPU-4x ≈ **$42.79/mo** but usage-billed, unpredictable,
and a steeper ops curve for zero benefit at one region. A managed PaaS costs ~2× and fixes nothing
that is actually broken. `DECISIONS.md:2473` (v1 locked to ≤100 users, K8s track descoped) is the
correct call and is well argued.

**The real bill is not the topology.** It is three things the topology does not excuse, in order:
(a) no armed backups (F7), (b) probably no external uptime probe (F1), (c) no auth-gated staging
URL (F9). (a) costs one env var. (c) costs $0. (b) costs $0–9/mo. Total honest spend to close all
three: **under $10/month and about half a day.** A second droplet is not needed — the ccstage stack
already exists on the box; it just isn't reachable.

**2 — Minimum credible alert set.**

*Page (wake the responder) — exactly one alert:*
- **Public `https://autoclip.studio/health` fails 2 consecutive checks from a third-party monitor.**
  Must probe the public hostname (so it covers the edge and the tunnel, not just the origin), from
  a vendor that cannot die with the host. Better Stack free (10 monitors / 3-min) or UptimeRobot
  Solo ≈ $7/mo — note UptimeRobot's free tier is **non-commercial only** since Oct 2024 and this
  product charges money. Must be verified against Bot Fight Mode before it is trusted
  (`EDGE_SECURITY.md:80-86`), and needs a documented mute step so an intentional poweroff doesn't
  page. **Nothing else pages.** One alert, one action: SSH in / reboot the droplet.

*Wait until morning (email/digest, never a phone):*
- **Backup heartbeat missed** — set `BACKUP_HEALTHCHECK_URL` to a healthchecks.io free check with a
  26 h grace. This is the single highest value-per-dollar alert in the system: $0, one env var, and
  it guards the only irreplaceable asset.
- **Deploy workflow failed** — already fires today via GitHub's actor email *because* the rollback
  correctly still exits 1. Verify the notification setting; that's the whole task.
- **Spend guard tripped / daily LLM spend over threshold** — emit from `billing/spend_guard.py`
  through the existing `notify/` + Resend rail. Do **not** wait on a metrics pipeline (F2).
- **Pipeline stalled** — "oldest video in a non-terminal status > 30 min" and "oldest unrendered
  auto-render clip > 60 min", computed by a Beat sweep as a plain SQL query. This is the alert that
  would have caught *"0 of 18 clips had ever rendered"* (`snag-taxonomy.md` §D3) and it needs no
  Prometheus at all.
- **Sentry new-issue digest** — once F3 confirms Sentry is actually live.
- **Droplet disk > 80%** — free in the DO console; pairs with F6.

Six signals. Five of them are emitted by code that already runs, into a mailer that already exists.
That is the correct shape for a solo responder and it is *less* infrastructure than what has
already been built.

**3 — RTO/RPO.** Should be **RPO 24 h / RTO 4 h**, drilled quarterly — which is already the recorded
decision. Current reality is RPO = total loss and RTO ≈ 1 h to an empty database. See F7 for the
timed walk-through.

**4 — Are Sentry and OTel live?** **Undeterminable from this repository, by construction.** See F3
for the three commands that answer it and the ~20-line change that makes it self-evident forever.

**5 — Is the ~50 s broken-image window acceptable?** At ≤100 users, **yes** — and I'd resist
blue-green here. The 50 s is 5×10 s of health retries that only elapse when the image is genuinely
broken (the loop breaks on first `ok`), so the healthy path is fast. The real exposure is not 50 s
of a bad image; it is the **rollback itself taking the tunnel and the database down** (F5), which is
a one-line fix and worth ten blue-green setups. If zero-downtime is later wanted, the 2026
lightweight path is Kamal or the `docker-ztd` Compose plugin with a small Traefik service — but
that adds a proxy hop in front of a Cloudflare Tunnel that is already doing that job, and I would
not spend it at this scale.

**6 — Deprecation hygiene.** *First, a factual correction to the premise:* the 35 MB `.mp4`,
`dump.rdb` and `{{pkgetc}}/` are **not in the repository** — `git check-ignore` resolves the mp4 to
`.gitignore:41` and `dump.rdb` to `.gitignore:34`; `{{pkgetc}}/` is untracked working-directory
cruft holding one broken symlink. They cost the developer's disk, nothing else. The 12 root PNGs
**are** tracked (~1.7 MB) but are excluded from the Docker build context by `.dockerignore`'s
root-scoped `*.png`, so they never reach the image either.

The actual committed debt is small and the policy is simple: **parked artifacts get deleted from
the trunk and preserved as a tag.** Delete `render.yaml` (F8 — it is armed, not merely stale),
`deploy/charts/` (12 files, never run, placeholder values, explicitly out of scope per
`AUDIT_BRIEF.md` §9), the 12 root PNGs and `notes_for_issues.txt` / `walkthrough.md` (move to
`docs/archive/` if they carry anything). Git tags are the archive; "we might need it" is not a
reason to keep a deployment trigger in the trunk of a push-to-deploy repo.

---

## What is genuinely right here

Naming these specifically, because they are better than most funded teams ship:

1. **The data-bearing staging gate** (`deploy.yml:31-147`). Deploying the exact `sha-` image (never
   `:latest`), migrating a *persistent* volume, asserting `alembic current == heads`, and keeping
   the volume by using `stop` not `down` — every one of those four choices exists because a real
   incident taught it, and the comments say which. This is the strongest gate in the pipeline and
   it is stronger than the industry norm for this scale.
2. **The rollback still exits 1** (`DECISIONS.md:11492`: *"auto-rollback without exit 1 would hide
   the deployment failure from alerting"*). Most teams get this wrong. Combined with
   `PREV_IMAGE` capture by **RepoDigest** — an immutable target, not a tag — and prune moved
   *after* the smoke so the rollback target survives.
3. **`sync_secret` never blanks a VM value** (`deploy.yml:243-247`). A missing GitHub secret leaves
   prod untouched. That guard is the difference between a partial secret set and an outage.
4. **`STORAGE_BACKEND=r2` pinned authoritatively rather than defaulted**, with the reason inline: a
   two-container topology makes local disk a *broken* backend. Written after it broke.
5. **The DR design** (F7) is textbook — the Object Lock Compliance-vs-Governance reasoning, the
   circular-dependency constraint on key escrow, retention by lifecycle rule so a script bug cannot
   mass-delete, and the mandatory `reapply_erasures.py` step after any restore. It only needs to be
   switched on.
6. **`tests/test_ci_config.py`** asserting properties of the deploy YAML itself (dump precedes
   alembic, prod `needs: deploy-staging`, staging is sha-pinned, compose parity). Testing the
   pipeline as an artifact is unusual and correct — it is the only mechanism in the repo capable of
   catching a Class-1 regression in deploy config.
7. **The `!cancelled()` comment at `deploy.yml:158-163`** — a genuinely subtle GitHub Actions
   semantic, explained in four lines so the next person cannot "simplify" it into a break-glass bug.

---

## Decisions this domain needs but does not have

1. **One authoritative "Deployment: current state and why" entry**, marking `DECISIONS.md:2541` and
   `:2563` (Render) **SUPERSEDED**. Four entries, two silently reversed, `render.yaml` still armed
   in the trunk. This is already named in `architecture-map.md` D4-15; F8 raises its priority from
   documentation hygiene to a live-config question.
2. **A stated RTO and RPO, with the drill date.** `RUNBOOKS.md:648` has the blank; fill it.
3. **An alerting policy: what pages vs. what waits.** There is no entry anywhere defining the
   severity ladder in terms of *signals*. `INCIDENT_RESPONSE.md` defines the ladder for incidents
   already known about; nothing defines how one becomes known.
4. **Where business metrics are supposed to land.** Prometheus counters, an OTel push rail that
   deliberately excludes them, and a Grafana panel spec that queries the counters. Three half-rails,
   no decision on which is canonical.
5. **A deprecation policy for parked artifacts** — the `architecture-map.md` D4-22 gap, still open,
   and F8 shows the cost of leaving it open is not theoretical.
6. **Log retention and rotation on the VM.** No entry, no config, unbounded by default.
7. **Whether staging should be continuously reachable**, and by whom. Currently an accident of
   `deploy.yml:146` rather than a decision.
8. **A single-VM failure-domain position.** `architecture-map.md` D4-17 names it. F1/F6/F7 are all
   downstream of it never having been written: nothing states what the VM is allowed to lose, how
   fast that must be noticed, or who notices.
