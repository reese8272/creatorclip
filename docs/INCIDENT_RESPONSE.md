# CreatorClip — Incident Response (Issue 283)

How incidents are classified, who responds (spoiler: you), how affected creators are
told, and where the step-by-step fix lives. This file is the **front door**; the
detailed procedures stay in `docs/RUNBOOKS.md` and `docs/runbooks/` — see the index
at the bottom. Every row is symptom → doc, so triage starts here, not in grep.

---

## Severity ladder

Three levels. Classify FIRST, then act — the level sets the clock.

| Level | Definition | Response target | Examples |
|-------|-----------|-----------------|----------|
| **SEV1** | Data loss or corruption, security/privacy breach, or a full outage (creators cannot log in, upload, or retrieve clips) | **Act now** — drop everything, around the clock until stable | Personal-data breach (GDPR 72h clock starts at awareness); `TOKEN_ENCRYPTION_KEY` or DB volume lost; OAuth tokens exposed in a log; app/worker down and not self-recovering |
| **SEV2** | Degraded pipeline — the product works but a core loop is broken or silently wrong for some/all creators | **Same day** — fix within the working day it is detected | Transcription or render jobs failing/retrying en masse; Celery Beat not firing (ToS purges stale); analytics refresh stuck; runaway LLM/transcription spend; storage sweep shows unexplained growth |
| **SEV3** | Annoyance — cosmetic, single-creator quirk, or non-urgent debt with a workaround | **Backlog** — file it in `docs/issues.md` (or `docs/OFF_COURSE_BUGS.md`) and schedule | One clip mis-framed; a flaky UI state; a noisy log line; a dashboard panel off by a day |

Escalate ambiguity upward: if you are debating SEV1 vs SEV2, it is SEV1 until proven
otherwise. Downgrading later is free; a late breach notification is not.

---

## Escalation — SOLO-RESPONDER model

There is **one responder and explicitly no on-call rotation**. Escalation means
escalating *your own attention*, not paging a teammate.

**How alerts arrive (all via email today):**

- **DigitalOcean billing alert** — spend threshold crossed on the VM account.
- **Better Stack** — uptime/heartbeat monitors on the public endpoints.
- **Grafana contact points** — alert rules routed to the email contact point.

**The loop:** email lands → classify against the ladder above → SEV1/SEV2: open the
matching runbook from the index below and start a timestamped scratch log of what you
observe and change (this becomes the post-incident record — the breach runbook
requires it for GDPR Art. 33(5)) → SEV3: file it and archive the alert.

**Future paging lever:** when email-only detection becomes the bottleneck, the
upgrade path is **Grafana Cloud IRM (free tier)** — on-call schedules, phone/push
paging, and alert deduplication at zero cost for a solo operator. Not wired up yet;
noted here so the decision is pre-made when it is needed.

---

## Communications templates

Honesty constraint applies to incident comms too: state facts and estimates, never
guarantees. Never include tokens, keys, or another creator's data in any message.

### Status-page post

```
[INVESTIGATING | IDENTIFIED | MONITORING | RESOLVED] — <short title>

Since <UTC time>, <plain-language symptom, e.g. "clip rendering is delayed">.
Impact: <who/what is affected, e.g. "new uploads queue but do not process">.
Your existing clips and account data are <unaffected / affected as described>.
Next update by <UTC time>.
```

### Affected-creator email skeleton

```
Subject: [CreatorClip] Service issue affecting your account — <date>

Hi <first name>,

Between <start UTC> and <end UTC>, <what happened in one plain sentence>.

What this meant for you: <specific impact, e.g. "the video you uploaded on
<date> did not finish processing">.

What we did: <fix in one sentence>.

What you need to do: <"Nothing — we re-ran the job" | specific action>.

We're sorry for the disruption. If anything still looks wrong, reply to this
email and we'll dig in.

— CreatorClip
```

For a personal-data breach, do NOT improvise from this skeleton — use the Art. 34
notification content requirements in the breach runbook (index below).

---

## Runbook index — symptom → doc

| Symptom | Severity guess | Runbook |
|---------|----------------|---------|
| Personal data exposed / suspected breach (tokens, PII, cross-creator leak) | SEV1 | `docs/RUNBOOKS.md` § Personal Data Breach Response (GDPR Art. 33 / Art. 34) — Issue 253 |
| `TOKEN_ENCRYPTION_KEY` lost, DB volume lost, R2 data deleted, or a migration destroyed data | SEV1 | `docs/RUNBOOKS.md` § Disaster Recovery, scenarios (a)–(d) — Issues 255–258; operator setup in `docs/runbooks/255-258-dr-durability.md` |
| `TOKEN_ENCRYPTION_KEY` suspected compromised (rotate, zero-downtime) | SEV1 | `docs/RUNBOOKS.md` § TOKEN_ENCRYPTION_KEY Rotation |
| `JWT_SECRET_KEY` suspected compromised / need to invalidate all sessions | SEV1 | `docs/RUNBOOKS.md` § JWT_SECRET_KEY Rotation |
| Queue lost, jobs vanished, Redis crashed or volume destroyed | SEV1–SEV2 | `docs/RUNBOOKS.md` § Redis broker durability & recovery — Issue 288 |
| Scheduled tasks not firing (stale analytics, ToS purges missed, no outcome polls) | SEV2 | `docs/RUNBOOKS.md` § Beat HA — RedBeat Recovery — Issue 263 |
| Runaway LLM / transcription spend (DO billing alert, cost panel spike) | SEV2 | Spend kill-switch runbook lands with Issues 290/291 (in flight); until then: § Monthly Cost Review in `docs/RUNBOOKS.md` to locate the source, then disable the offending feature flag |
| Creator demands money back / billing dispute | SEV2–SEV3 | `docs/RUNBOOKS.md` § Money Refund — Issue 208 |
| Lifecycle/notification emails bouncing or landing in spam | SEV2–SEV3 | `docs/RUNBOOKS.md` § Email Deliverability — SPF / DKIM / DMARC |
| Publish-to-YouTube failing (upload scope, OAuth verification) | SEV2 | `docs/runbooks/194-youtube-publish.md` |
| Prod env/config gates for the beta deploy (secrets, OAuth consent, domains) | SEV2–SEV3 | `docs/runbooks/24-25-26-beta-deploy-gates.md` |
| K8s/GKE staging deploy issues (Helm chart, cluster bring-up) | SEV3 | `docs/runbooks/275-279-k8s-deploy.md` |
| CI broken (pre-push gate or self-hosted runner) | SEV3 | `docs/runbooks/local-ci-cd.md` |
| Monthly COGS review shows drift or a per-creator cost outlier | SEV3 | `docs/RUNBOOKS.md` § Monthly Cost Review — Issue 292 |

If the symptom matches no row: classify by the ladder, start the scratch log, and add
a row here once the incident is understood.
