# Research Brief 10 — Disaster Recovery, Backups & Data Durability (Issue 175)

**Author:** read-only research agent · **Date:** 2026-06-22
**Drives:** Issue 175 (Phase 1 CHECK) → sub-issues below
**Scope:** What survives the death of the single beta VM. Backups, restore, failover, and data
durability for Postgres, Redis, Cloudflare R2, and — most urgently — the encryption key + `.env`.
**Method:** current industry standard researched first (links inline); every repo claim cited
`file_path:line`. Where I could not verify a claim, I say so explicitly.

> Guardrails this brief respects throughout (the Hard Constraints in the prompt): encrypted token
> columns stay encrypted in every backup; `TOKEN_ENCRYPTION_KEY` must be recoverable **separately**
> from the DB (a lost key bricks all tokens); backups honor data-retention/ToS (source-media purge,
> `worker/schedule.py:30`) and must not become an unmanaged PII copy; **no secret in any backup
> tooling log or in git**.

---

## 1. Executive summary — the conclusions, up front

1. **The single biggest data-loss risk today is not the database — it is the encryption key and the
   `.env`, which exist in exactly one place on Earth.** `TOKEN_ENCRYPTION_KEY`, `JWT_SECRET_KEY`,
   and every provider secret live only in `/opt/autoclip/.env` (chmod 600) on the beta VM
   (`docs/RUNBOOKS.md:31`, `docs/SECRETS.md:36`, `docs/ACCESS.md:257`). There is **no documented
   off-box copy anywhere**. If that droplet's disk is lost, `TOKEN_ENCRYPTION_KEY` is gone, and
   **every `access_token_encrypted` / `refresh_token_encrypted` in `youtube_tokens` becomes
   permanently undecryptable** — even a perfect Postgres restore would yield ciphertext no key can
   open (Fernet is authenticated; `docs/RUNBOOKS.md:11`). That single file is the keystone of the
   North Star: lose it and every creator must re-auth from scratch, and any DB backup of the token
   table is worthless. **This is the minimum thing to fix before onboarding more creators.**

2. **Nothing is backed up today. Full stop.** A repo-wide search for `pg_dump`, `pg_basebackup`,
   `wal-g`/`wal-e`, `pgBackRest`, `barman`, R2 versioning/lock config, or any `backup`/`restore`
   script returns **only prose in docs** — no tooling in `scripts/`, no compose service, no cron,
   no GitHub Action. Postgres lives on a single Docker named volume (`postgres_data`,
   `docker-compose.prod.yml:68-69,112`) on one VM. There is no automated dump, no WAL archive, no
   off-box copy. The deploy pipeline runs `alembic upgrade head` against prod **with no backup
   first** (`scripts/deploy.sh` "Running migrations"; `.github/workflows/deploy.yml`) — a bad
   migration has no undo. The rotation runbook *says* "a database backup has been taken"
   (`docs/RUNBOOKS.md:31`) but there is no mechanism that takes one.

3. **The beta topology is a stack of single points of failure**, by design (it's a beta): one VM,
   one Postgres, one Redis, one beat pod. A dead VM today = total outage **and** total data loss
   (DB + secrets + any local media). This is acceptable risk for a closed beta **only** once (1)
   the key/secret is escrowed and (2) Postgres has an automated off-box backup — those two are the
   floor.

4. **R2 is extremely durable but not self-protecting against your own mistakes.** R2 advertises
   eleven 9s of annual durability via replication + erasure coding
   ([Cloudflare R2 durability docs](https://developers.cloudflare.com/r2/reference/durability/)),
   so hardware loss of *rendered clips* is a non-issue. But R2 **does not offer GA object
   versioning** (the S3 "keep previous versions" feature) — the
   [R2 buckets index](https://developers.cloudflare.com/r2/buckets/) lists Bucket locks,
   lifecycle, storage classes, event notifications, CORS — **no versioning page**. So an accidental
   or malicious delete/overwrite of an object is **not** recoverable the way it would be on S3.
   The available mitigation is **R2 Bucket Locks** (WORM/immutability, GA 2025-03-06:
   ["Set retention policies… with bucket locks"](https://developers.cloudflare.com/changelog/2025-03-06-r2-bucket-locks/)),
   which prevent deletion/overwrite for a set period and **take precedence over lifecycle rules**.
   This is the correct R2-durability lever for this project, not versioning.

5. **The good news: most of the data model is regenerable, and the precious slice is small.** The
   irreplaceable set is narrow — creator records, the encrypted tokens (worthless without the
   key), the trained taste (`clip_feedback` / `clip_outcomes` / `preference_models` /
   `creator_dna` / `creator_identity`), and the billing ledgers. That whole slice is small,
   text/blob, and fits a nightly logical `pg_dump` comfortably. **A single nightly encrypted
   `pg_dump` pushed to a separate R2 bucket + a one-time key escrow gets the beta from "one disk
   death = company over" to "one disk death = a day of work and ≤24h of lost feedback."**

**Minimum posture before more creators onboard (the floor):** (a) escrow
`TOKEN_ENCRYPTION_KEY` + `JWT_SECRET_KEY` + a copy of `.env` off-box in a password manager / GCP
Secret Manager; (b) nightly encrypted `pg_dump` to a separate R2 bucket with a Bucket Lock; (c) one
**tested** restore drill. Everything else (PITR, Cloud SQL, failover) is the K8s-target tier.

---

## 2. What the current standard says (researched first)

- **Postgres logical vs physical.** `pg_dump` = portable, object-level, version-independent logical
  backup; slower to restore; **cannot** participate in PITR (a dump has no WAL).
  `pg_basebackup` + continuous WAL archiving = physical base backup that enables **Point-in-Time
  Recovery** (replay WAL forward to any moment). The robust pattern combines both: physical+WAL for
  DR/PITR, periodic logical for portability and selective restore. For larger datasets, **pgBackRest**
  adds incremental/parallel/encrypted backups to S3-compatible storage and simplifies PITR.
  ([PostgreSQL: Continuous Archiving & PITR](https://www.postgresql.org/docs/current/continuous-archiving.html),
  [Stormatics — PostgreSQL Backup Best Practices](https://stormatics.tech/blogs/postgresql-backup-best-practices),
  [Percona — Enterprise PostgreSQL Backup Strategy](https://www.percona.com/blog/postgresql-backup-strategy-enterprise-grade-environment/))
  **For a beta of this size, nightly `pg_dump` is the right tool;** PITR is overkill until RPO must
  drop below ~24h.
- **Managed target (Cloud SQL).** Cloud SQL for PostgreSQL provides automated daily backups +
  PITR via WAL. Default automated-backup retention is **7 days** (Enterprise) / **15 days**
  (Enterprise Plus), configurable 1–365; PITR transaction-log retention is **7 days** (1–7
  Enterprise / 1–35 Enterprise Plus), and log retention must be ≤ backup retention.
  ([Cloud SQL backups overview](https://docs.cloud.google.com/sql/docs/postgres/backup-recovery/backups),
  [Cloud SQL PITR](https://docs.cloud.google.com/sql/docs/postgres/backup-recovery/pitr))
  This matches the stated GKE/Cloud SQL target (`docs/DEPLOYMENT.md:43`) and largely **outsources**
  the DB-backup problem — but **not** the key-escrow problem.
- **Object storage (R2).** Eleven-9s durability; **no GA object versioning**; use **Bucket Locks**
  (immutability/retention, precedence over lifecycle) to protect against accidental/malicious
  delete; use **Object Lifecycle** rules to auto-expire (already needed for source-media retention).
  ([R2 durability](https://developers.cloudflare.com/r2/reference/durability/),
  [R2 buckets](https://developers.cloudflare.com/r2/buckets/),
  [R2 bucket locks changelog](https://developers.cloudflare.com/changelog/2025-03-06-r2-bucket-locks/),
  [R2 object lifecycles](https://developers.cloudflare.com/r2/buckets/object-lifecycles/))
- **Redis durability.** RDB (snapshot, fast restart, accepts loss) vs AOF (logs every write, higher
  durability); hybrid (RDB+AOF) is the production default. **For a Celery broker, in-flight task
  loss on a Redis death is normally acceptable** if tasks are idempotent and re-triggerable — the
  durable record is in Postgres, not Redis.
  ([Redis persistence docs](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/))
- **3-2-1 + RPO/RTO.** 3 copies, 2 media types, 1 off-site; modern variants add immutability
  (3-2-1-1-0). **RPO** = max acceptable *data loss* (time between recoverable points); **RTO** =
  max acceptable *downtime*. An untested backup is not a backup — drills are part of the standard.
  ([Veeam — RTO vs RPO](https://www.veeam.com/blog/recovery-time-recovery-point-objectives.html),
  [Rubrik — 3-2-1 rule](https://www.rubrik.com/insights/understanding-the-3-2-1-backup-rule))

---

## 3. Data-criticality table (store → class → RPO/RTO → current backup → recommended)

RPO/RTO are **proposed targets for the beta tier** (low-cost, manual restore acceptable). Tighten
at the Cloud SQL/K8s tier.

| Store / data | Precious or regenerable? | Proposed RPO | Proposed RTO | Current backup state | Recommended backup |
|---|---|---|---|---|---|
| **`TOKEN_ENCRYPTION_KEY`, `JWT_SECRET_KEY`, full `.env`** | **PRECIOUS — keystone.** Key loss bricks all tokens (`docs/RUNBOOKS.md:11`); no regen path. | **0** (must never lose) | minutes | **NONE off-box.** One copy in `/opt/autoclip/.env` chmod 600 (`docs/SECRETS.md:36`). | **Escrow now**: copy into a password manager **and** GCP Secret Manager (the chosen secrets backend, `docs/DEPLOYMENT.md:45`). Document recovery in `RUNBOOKS.md`. |
| **`creators`, `youtube_tokens`** | PRECIOUS. Tokens re-obtainable only by every creator re-OAuthing; `creators` is identity/billing anchor. | ≤24h | ≤4h | NONE (single Docker volume). | Nightly `pg_dump` (whole DB) → separate R2 bucket, encrypted. Tokens stay ciphertext in the dump (column-level encryption is preserved by `pg_dump`). |
| **`creator_dna`, `creator_identity`** | PRECIOUS — the inferred + stated channel knowledge (the moat). DNA *could* be re-synthesized from YouTube analytics at LLM cost + creator re-confirmation; identity is **append-only stated input, not regenerable** (`SOT.md:320`). | ≤24h | ≤4h | NONE. | Same nightly `pg_dump`. |
| **`clip_feedback`, `clip_outcomes`, `preference_models`** | **PRECIOUS — irreplaceable trained taste.** `clip_outcomes` is "the strongest positive signal" (`SOT.md:340`); cannot be reconstructed from YouTube. | ≤24h | ≤4h | NONE. | Same nightly `pg_dump`. |
| **`usage`, `minute_deductions`, `audit_log`** | PRECIOUS — billing truth + audit trail (`SOT.md:352-361`). Financial/compliance record. | ≤24h (ideally 0) | ≤4h | NONE. | Same nightly `pg_dump`. (Consider tighter RPO when real billing is live — Issue 171.) |
| **`videos`, `video_metrics`, `retention_curves`, `audience_activity`, `demographics`** | REGENERABLE from YouTube APIs (re-fetched by `refresh_youtube_analytics`, `worker/schedule.py:34`); also auto-purged at 30d per ToS (`worker/schedule.py:38`). | ≤7d | ≤1d | NONE. | Covered by the same nightly dump (cheap to include); not worth separate handling. |
| **`transcripts`, `signals`, `dna_embeddings`** | REGENERABLE — re-derivable by re-running transcribe/signals/embeddings, but at WhisperX/LLM/Voyage **compute cost**. Lose them and you pay to rebuild. | ≤7d | ≤1d | NONE. | Covered by the nightly dump; cheaper to back up than to recompute. |
| **`clips` rows (metadata) + `clip_feedback` link** | PRECIOUS for the feedback join; the row is small. | ≤24h | ≤4h | NONE. | Nightly dump. |
| **R2: source media** (`origin=upload`) | REGENERABLE/EPHEMERAL — purged at `SOURCE_MEDIA_RETENTION_HOURS` (default 72) per ToS (`worker/schedule.py:30`, `SOT.md:446`). Must **not** be retained long. | n/a | n/a | R2 11-nines durability; lifecycle purge. | **Do not back up** (retention/ToS forbids a long-lived copy). Add a lifecycle rule mirroring the purge as defense-in-depth. |
| **R2: rendered clips** (`render_uri`) | SEMI-PRECIOUS — re-renderable from source *if source still exists*, but source is purged at 72h, so after that a lost render is **gone**. The creator may not have downloaded it yet. | ≤24h (best-effort) | best-effort | R2 11-nines durability; **no versioning** ⇒ accidental delete is unrecoverable. | Enable an **R2 Bucket Lock** (retention ≥ a few days) on the clips prefix so a bad `delete_prefix` (`worker/storage.py:78`) can't wipe undelivered renders. |
| **Redis** (Celery broker + short-lived cache) | EPHEMERAL — broker + cache only (`SOT.md:23,42`); durable state is in Postgres. AOF is on (`docker-compose.prod.yml:81`). | accept in-flight loss | minutes (restart) | `appendonly yes`; single instance, no off-box copy. | Keep AOF on; **no backup needed.** Ensure tasks are idempotent + re-triggerable (already a project rule, `CLAUDE.md`). Document "Redis loss = re-trigger in-flight jobs." |

---

## 4. Backup + restore plan

### 4a. Beta-now (single VM) — the floor, in priority order

**P0 — Secret/key escrow (do first; cheap; closes the keystone risk).**
- Copy `TOKEN_ENCRYPTION_KEY`, `JWT_SECRET_KEY`, and a full snapshot of `/opt/autoclip/.env` into
  **two** independent off-box locations: (1) a personal password manager (1Password/Bitwarden),
  and (2) **GCP Secret Manager** (already the chosen prod secrets backend, `docs/DEPLOYMENT.md:45`
  — adopting it now de-risks the migration too).
- **Never** put these in git, CI logs, or a backup that lands beside the DB dump. The escrow copy
  must live somewhere a dead VM cannot take with it.
- Re-escrow on every rotation (fold a step into `docs/RUNBOOKS.md` → TOKEN_ENCRYPTION_KEY Rotation,
  which currently has no "update the escrow copy" step).
- **Compliance note:** the escrow holds secrets, not PII. It does not change data-retention posture.

**P1 — Nightly encrypted Postgres dump, off the VM.**
- A small script (`scripts/backup_pg.sh`, new) runs `pg_dump` inside the postgres container, pipes
  through `gpg`/`age` symmetric encryption (key from `.env`/Secret Manager, **never logged**), and
  `aws s3 cp`s the ciphertext to a **separate** R2 bucket (`creatorclip-backups`, distinct from the
  media bucket so a media-bucket mistake can't touch backups).
- Schedule via host cron or a `beat` task (cron keeps it independent of app health).
- **Retention:** keep ~14 dailies + ~8 weeklies. This satisfies 3-2-1 minimally (copy 1 = live DB
  volume; copy 2 = R2 dump; "off-site" = R2 is a different provider than DigitalOcean).
- **Bucket Lock** on the backup bucket (e.g. 14-day retention) so a compromised VM credential
  can't delete the backups (ransomware/immutability per
  [R2 bucket locks](https://developers.cloudflare.com/changelog/2025-03-06-r2-bucket-locks/)).
- **Encryption-preservation:** `pg_dump` copies `*_encrypted` columns verbatim (still Fernet
  ciphertext) — so the dump is safe *and* useless without the separately-escrowed key. Belt: the
  whole dump is also encrypted at rest in R2.
- **Compliance note:** the dump contains creator emails + aggregated demographics (PII). Lock the
  backup bucket private, encrypt at rest, and add the backup bucket to the data-retention register
  (`docs/COMPLIANCE.md`) so it's purged with the rest on erasure. The dump's `videos`/analytics
  rows are still subject to the 30-day staleness rule — a 14-day backup window stays inside it.

**P1 — Pre-migration safety dump in the deploy pipeline.**
- `scripts/deploy.sh` / `deploy.yml` runs `alembic upgrade head` against prod with no backup
  (`scripts/deploy.sh` "Running migrations"). Add a `pg_dump` step **before** the migration so a
  bad schema change has an undo. Gate the rollout on the dump succeeding.

**P2 — R2 protections.**
- Add a **Bucket Lock** on the rendered-clips prefix (short retention) so `delete_prefix`
  (`worker/storage.py:78`) — which is unfiltered by design — cannot wipe undelivered renders.
- Add a **lifecycle rule** mirroring `SOURCE_MEDIA_RETENTION_HOURS` on the source-media prefix as
  defense-in-depth behind the beat purge.

**P3 — Document Redis loss as accepted.** No backup; on Redis loss, restart and re-trigger
in-flight jobs (idempotent by project rule). Record the RPO=in-flight-only decision.

### 4b. K8s / Cloud SQL target — the durable tier

- **Postgres → Cloud SQL** (`docs/DEPLOYMENT.md:43`): enable **automated daily backups** + **PITR**.
  Set backup retention to the policy window (default 7d Enterprise; raise toward 30d if the
  data-retention register allows — cap at the analytics-staleness ceiling). This outsources DB DR.
  ([Cloud SQL backups](https://docs.cloud.google.com/sql/docs/postgres/backup-recovery/backups))
- **Keep an independent logical export.** Cloud SQL backups can't be restored *outside* Cloud SQL.
  Keep a weekly `pg_dump`/Cloud SQL export to R2/GCS so you're not locked to one provider's restore
  path (the "2 different media/systems" leg of 3-2-1).
- **Secrets → GCP Secret Manager + External Secrets Operator** (`docs/DEPLOYMENT.md:45`). The key
  is now managed, replicated, and versioned by GCP — the keystone risk is structurally solved.
  **Still keep one out-of-band escrow** of `TOKEN_ENCRYPTION_KEY` (a GCP project deletion / billing
  lapse is its own failure mode).
- **Redis** managed or in-cluster; broker loss still acceptable; KEDA/beat re-trigger covers it.
- **Failover:** Cloud SQL HA (regional, standby) for RTO in minutes; multi-replica app pods; beat
  is a single replica (`docs/DEPLOYMENT.md:65`) — a beat outage delays purge/refresh/outcome polls
  but loses no durable data (those tasks are idempotent catch-up sweeps).

---

## 5. DR runbook outline + the drill (an untested backup is not a backup)

Proposed new `docs/RUNBOOKS.md` section: **"Disaster Recovery."** Outline:

**Failure mode (a) — the VM dies.**
1. Provision a new DigitalOcean droplet (or restore the DO snapshot if one exists).
2. Restore `/opt/autoclip/.env` from the **escrow** (P0) — this is the step that makes everything
   else usable; without the escrowed `TOKEN_ENCRYPTION_KEY` the token rows are dead.
3. `docker compose -f docker-compose.prod.yml up -d postgres redis`.
4. `alembic upgrade head` (schema), then restore the latest dump (decrypt → `pg_restore`/`psql`).
5. Bring up `app worker beat cloudflared`; verify `/health` = ok and one creator's YouTube refresh
   decrypts cleanly (proves the key↔ciphertext pairing survived).
6. Repoint the Cloudflare Tunnel if the new box needs a fresh connector (`docs/ACCESS.md:200`).
   **RTO target: ≤4h.**

**Failure mode (b) — Postgres corrupts (disk OK).**
- Stop the app, restore the latest nightly dump into a fresh DB, `alembic upgrade head` if needed,
  verify row counts of the precious tables, swap `DATABASE_URL`. **RPO: ≤24h** (last nightly).

**Failure mode (c) — R2 data lost / wrongly deleted.**
- Rendered clips: if within the Bucket-Lock window, the objects were never deletable — confirm and
  move on. Outside it: re-render from source **only if source still within 72h retention**;
  otherwise the render is unrecoverable — communicate honestly. Source media: by policy it's
  purgeable; no restore.

**Failure mode (d) — the encryption key is lost (the worst case).**
- If escrow exists (P0): restore the key, done. **If no escrow exists: the tokens are
  unrecoverable** — there is no cryptographic path back. Recovery = force every creator to
  re-connect YouTube (re-OAuth re-populates `youtube_tokens` under the *new* key); all other data
  in the DB restore is fine because only the token columns are encrypted. This failure mode is
  exactly why P0 is P0.

**The drill (must be scheduled, e.g. quarterly):** on a throwaway droplet, restore the latest
encrypted dump + escrowed `.env` end-to-end, bring the stack up, and assert (1) `/health` ok,
(2) a known creator's token `decrypt()`s without `TokenDecryptError`, (3) `preference_models` /
`clip_outcomes` row counts match expectation. Record the measured RTO. A drill that has never been
run is the most common cause of a "backup" that turns out to be unrestorable.

---

## 6. Proposed issues (dependency-ordered, `docs/issues.md` house style)

> Numbering: Issue 175 is the umbrella (this brief). Sub-issues proposed as **175a–175e** so they
> slot under the existing research→issue mapping (`docs/research/README.md:38`). Each notes the
> `DECISIONS.md` / `RUNBOOKS.md` entry it requires.

### Issue 175a: Off-box escrow of `TOKEN_ENCRYPTION_KEY`, `JWT_SECRET_KEY` & `.env` (the keystone)
**Severity**: SEV-0 — a single disk loss permanently bricks every YouTube token.
**Depends on**: none. **Do this first.**
**What**: Establish a documented, out-of-band copy of the irreplaceable secrets so a dead VM does
not equal permanently undecryptable tokens. Copy `TOKEN_ENCRYPTION_KEY`, `JWT_SECRET_KEY`, and a
snapshot of `/opt/autoclip/.env` into (1) a password manager and (2) GCP Secret Manager
(`docs/DEPLOYMENT.md:45`). Add an "update the escrow" step to the rotation runbook.
**Acceptance criteria**:
- [ ] `TOKEN_ENCRYPTION_KEY` + `JWT_SECRET_KEY` + `.env` snapshot stored in two independent off-box
      locations; neither in git nor any CI/backup-tool log.
- [ ] `docs/RUNBOOKS.md` "TOKEN_ENCRYPTION_KEY Rotation" gains a step: re-escrow after Step 4.
- [ ] A new `docs/RUNBOOKS.md` "Disaster Recovery → key loss" entry documents the restore-from-escrow
      path **and** the no-escrow fallback (force re-OAuth).
- [ ] **`docs/DECISIONS.md`** entry: GCP Secret Manager adopted as the secret-escrow backend in beta
      (pulls the K8s-target choice forward).

### Issue 175b: Nightly encrypted Postgres backup to a separate R2 bucket + tested restore
**Severity**: SEV-1 — currently zero DB backups; one disk loss = total data loss.
**Depends on**: 175a (so a restore is actually usable).
**What**: `scripts/backup_pg.sh` runs `pg_dump`, encrypts (age/gpg, key from Secret Manager, never
logged), uploads to `creatorclip-backups` R2 bucket; scheduled nightly via host cron. Retention
~14 daily + 8 weekly. Bucket Lock on the backup bucket. Add the backup bucket to the
data-retention register.
**Acceptance criteria**:
- [ ] `scripts/backup_pg.sh` produces an encrypted dump in a **separate** R2 bucket from media; no
      secret appears in its output/logs.
- [ ] Nightly schedule live (cron or beat) with success/failure visibility.
- [ ] R2 **Bucket Lock** on the backup bucket (≥14d retention) verified.
- [ ] **A documented, executed restore drill** (§5) on a throwaway target: `/health` ok + one
      creator token decrypts + precious-table row counts match. Measured RTO recorded.
- [ ] `.env.example` documents any new backup config (bucket, encryption key var).
- [ ] `docs/COMPLIANCE.md` lists the backup bucket under data-retention (PII-bearing, purged on
      erasure, within the 30-day analytics-staleness window).
- [ ] **`docs/RUNBOOKS.md`** "Disaster Recovery" section added (failure modes a–d + the drill).

### Issue 175c: Pre-migration safety dump in the deploy pipeline
**Severity**: SEV-2 — a bad `alembic upgrade head` runs against prod with no undo today.
**Depends on**: 175b (reuses the dump tooling).
**What**: Add a `pg_dump` step to `scripts/deploy.sh` + `.github/workflows/deploy.yml` **before**
`alembic upgrade head`; gate the rollout on the dump succeeding; keep the last N pre-deploy dumps.
**Acceptance criteria**:
- [ ] Deploy takes (and verifies) a dump before migrating; rollout aborts if the dump fails.
- [ ] Rollback note in `docs/RUNBOOKS.md` references the pre-deploy dump.

### Issue 175d: R2 durability hardening — Bucket Lock on renders + lifecycle on source media
**Severity**: SEV-2 — `delete_prefix` (`worker/storage.py:78`) can wipe undelivered renders; R2 has
no versioning to undo it.
**Depends on**: none (R2-config only).
**What**: Enable an R2 **Bucket Lock** (short retention) on the rendered-clips prefix; add a
**lifecycle rule** mirroring `SOURCE_MEDIA_RETENTION_HOURS` on the source-media prefix as
defense-in-depth behind the beat purge.
**Acceptance criteria**:
- [ ] Bucket Lock active on the clips prefix; a test delete within the window is rejected.
- [ ] Lifecycle rule expires source media in line with `SOURCE_MEDIA_RETENTION_HOURS`; documented
      as belt-and-suspenders for `worker/schedule.py:30`.
- [ ] **`docs/DECISIONS.md`** entry: R2 has no GA object versioning; Bucket Locks chosen as the
      delete-protection mechanism (with the evidence link).

### Issue 175e (K8s target, deferred): Cloud SQL automated backups + PITR + independent export
**Severity**: SEV-2 — durable-tier DR; lands with the GKE/Cloud SQL migration.
**Depends on**: the GKE/Cloud SQL cutover (`docs/DEPLOYMENT.md:43`).
**What**: On Cloud SQL, enable automated daily backups + PITR (retention per policy); keep a weekly
independent `pg_dump`/export to R2/GCS so restore isn't locked to one provider; enable Cloud SQL HA
for minutes-RTO failover. Retire the beta cron dump once verified.
**Acceptance criteria**:
- [ ] Automated backups + PITR enabled; retention set and recorded; PITR-log ≤ backup retention.
- [ ] Weekly independent export to object storage verified restorable.
- [ ] HA (regional/standby) enabled; failover drill documented.
- [ ] **`docs/DECISIONS.md`**: chosen retention windows + the "keep an independent export despite
      managed backups" rationale.

---

## 7. Open questions for the human (one-line answers)

1. **Acceptable beta RPO for the precious tables — is 24h (nightly dump) fine, or do you want
   tighter (e.g. 6h / PITR) before more creators?**
2. **Escrow backend for the key — GCP Secret Manager now (pulls the prod choice forward), or just a
   password manager until the K8s migration?**
3. **Backup retention window — 14 daily + 8 weekly OK, or different? (Must stay inside the 30-day
   analytics-staleness ceiling for the analytics rows it carries.)**
4. **Who/what runs the nightly dump — host cron on the VM, or a Celery beat task?** (cron survives
   app outages; beat reuses existing infra.)
5. **Do you want a DO droplet snapshot too** (whole-box image, captures the volume + `.env`), as a
   coarse belt alongside the logical dump — accepting it then holds an unmanaged secret+PII copy?
6. **Is a quarterly restore drill cadence acceptable, or should it be monthly given the beta is
   actively onboarding?**

---

## 8. Flagged stale / contradictory docs

- **`docs/RUNBOOKS.md:31`** pre-flight says "A database backup has been taken (belt-and-suspenders)"
  — but **no backup mechanism exists** in the repo. The runbook implies a capability that isn't
  built. Resolve via 175b (make it real) or soften the wording until then.
- **`docs/SOT.md:461`** still lists "`TOKEN_ENCRYPTION_KEY` rotation runbook not yet written" under
  Known Production Gaps, but it **is** written (`docs/RUNBOOKS.md:5`, consolidated in Issue 146).
  Stale gap line — should be struck. (Off-course relative to this brief; noted, not chased.)
- **R2 expectations:** any place assuming S3-style object versioning on R2 would be wrong — R2 has
  no GA versioning (§2). I did not find such an assumption in the docs, but the team should know
  before relying on "we can just restore a previous version."
