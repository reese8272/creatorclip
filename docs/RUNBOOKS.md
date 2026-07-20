# CreatorClip — Operations Runbooks

---

## Email Deliverability — SPF / DKIM / DMARC DNS Rollout (autoclip.studio)

**When to run:** Before setting `NOTIFY_BACKEND=resend` in production. All three
DNS records must resolve and pass DMARC alignment before sending any email from
`@autoclip.studio`. Google / Yahoo / Microsoft reject non-compliant mail at SMTP
level as of 2026 for bulk senders.

**Estimated time:** 30 min DNS setup + up to 48 hours propagation; DMARC tighten
happens over weeks as reports confirm no false positives.

**Sources:**
- https://resend.com/docs/dashboard/domains/introduction (Resend domain setup)
- https://inboxstack.com/blog/dmarc-dkim-spf-email-authentication-guide-2026
- https://dmarcbusta.com/blog/complete-guide-to-dmarc-implementation-2026

---

### Step 1 — Verify the sending domain in Resend

1. Resend dashboard → Domains → Add Domain → enter `autoclip.studio`.
2. Resend will display the DNS records that must be added (SPF, DKIM, DMARC).
3. Do **not** click "Verify" until the records are live and propagated.

---

### Step 2 — Add SPF record

Add a TXT record to `autoclip.studio`:

```
Type:    TXT
Host:    @  (or autoclip.studio)
Value:   v=spf1 include:amazonses.com ~all
```

Notes:
- Resend routes email through Amazon SES infrastructure; `include:amazonses.com`
  is the correct SPF mechanism as of 2026.
- Use `~all` (softfail) not `-all` (hardfail) until DMARC is stable.
- SPF has a hard 10-DNS-lookup limit; do not daisy-chain more than needed.
- If you already have an SPF record, merge the `include:` — only one TXT SPF
  record is allowed per hostname.

---

### Step 3 — Add DKIM record (2048-bit RSA)

Resend generates the DKIM keypair and provides the public key as a TXT record:

```
Type:    TXT
Host:    resend._domainkey.autoclip.studio
Value:   v=DKIM1; k=rsa; p=<Resend-provided 2048-bit public key>
```

Notes:
- 2048-bit RSA is the current standard; 1024-bit is phased out and rejected by
  major inbox providers as of 2025. 4096-bit causes DNS size issues (UDP packet
  fragmentation). Do not manually generate keypairs — use Resend's generated key.
- Resend rotates DKIM keys periodically; check the dashboard after any key
  rotation notice and update the DNS record.

---

### Step 4 — Add DMARC record (start p=none with reporting)

Add a TXT record to `_dmarc.autoclip.studio`:

```
Type:    TXT
Host:    _dmarc.autoclip.studio
Value:   v=DMARC1; p=none; rua=mailto:dmarc-reports@autoclip.studio; sp=none; adkim=r; aspf=r
```

Notes:
- **Start with `p=none`** — this reports failures without rejecting mail.
  Setting `p=reject` on day 1 can silently blackhole legitimate transactional
  mail if alignment is off.
- `rua=mailto:dmarc-reports@autoclip.studio` sends aggregate reports to that
  address; create it or use a free DMARC reporting service (e.g. dmarc.postmarkapp.com).
- Review reports after 1–2 weeks of real traffic.

---

### Step 5 — Verify in Resend and test

After DNS propagates (check with `dig TXT autoclip.studio`, `dig TXT resend._domainkey.autoclip.studio`,
`dig TXT _dmarc.autoclip.studio`):

1. Resend dashboard → Domains → click "Verify". All three records should show green.
2. Send a test email from the Resend dashboard to a Gmail address.
3. In Gmail, open the email → three-dot menu → "Show original" → confirm:
   - `SPF: PASS`
   - `DKIM: PASS`
   - `DMARC: PASS`

---

### Step 6 — Tighten DMARC over time

**Do not rush this.** Only tighten after DMARC aggregate reports show zero
unexplained failures for 1–2 weeks of live traffic.

```
# Week 1-2: p=none (monitoring)
v=DMARC1; p=none; rua=mailto:dmarc-reports@autoclip.studio; sp=none

# Week 2-4: p=quarantine (spam-folder suspicious mail)
v=DMARC1; p=quarantine; rua=mailto:dmarc-reports@autoclip.studio; pct=10

# Once clean at pct=100 quarantine: p=reject
v=DMARC1; p=reject; rua=mailto:dmarc-reports@autoclip.studio
```

Log each policy change in `docs/DECISIONS.md`.

---

### Step 7 — Set production env vars

After verified green:

```bash
# .env (VM, /opt/autoclip/.env):
NOTIFY_BACKEND=resend
RESEND_API_KEY=re_...     # from Resend dashboard → API Keys
EMAIL_FROM=noreply@autoclip.studio
```

Restart: `docker compose up -d`

---

## TOKEN_ENCRYPTION_KEY Rotation

### Background

`TOKEN_ENCRYPTION_KEY` is a Fernet symmetric key used to encrypt YouTube OAuth tokens
(`access_token_encrypted`, `refresh_token_encrypted`) stored in the `youtube_tokens` table.
Fernet encryption is authenticated — a ciphertext produced with key A cannot be decrypted
with key B, so rotation requires a re-encryption step while the app is operational.

### When to rotate

- Suspected key exposure (e.g. committed to version control, leaked via logs)
- Scheduled rotation per your security policy (recommended: every 90 days)
- Before the first production deployment if the key was ever used in a non-production env

### Why this is zero-downtime

`crypto._fernet()` builds a `MultiFernet([primary, previous])`: `encrypt()` always uses the
primary (`TOKEN_ENCRYPTION_KEY`); `decrypt()` tries primary **then** previous
(`TOKEN_ENCRYPTION_KEY_PREVIOUS`). Setting `PREVIOUS` = the current key *before* re-encrypting
means tokens stay readable under either key throughout — **no maintenance window needed.**

### Pre-flight checklist

- [ ] Read access to the production database; write access to the secrets store (`.env`
      at `/opt/autoclip/.env` chmod 600, or the Kubernetes Secret) — keys live there, never in git
- [ ] A database backup exists (belt-and-suspenders; the re-encrypt is atomic). The nightly
      `scripts/backup_pg.sh` (Issue 256) covers this; to force a fresh one now, run it manually
      on the VM — see **Disaster Recovery → (b) Database loss**.

---

### Step 1 — Generate a new key

```bash
docker compose -f docker-compose.prod.yml exec -T app \
  python -c "from crypto import generate_key; print(generate_key())"
```

Copy the output (44 base64 chars). Call it `NEW_KEY`; the current key is `OLD_KEY`.

### Step 2 — Enter the decrypt-both window

In the secrets store set `TOKEN_ENCRYPTION_KEY_PREVIOUS` to the **current** key, leaving
`TOKEN_ENCRYPTION_KEY` unchanged, then restart so the app can decrypt under either key:

```
# .env: TOKEN_ENCRYPTION_KEY=<OLD_KEY>   TOKEN_ENCRYPTION_KEY_PREVIOUS=<OLD_KEY>
docker compose -f docker-compose.prod.yml up -d app worker beat
# Kubernetes: patch the Secret, then `kubectl rollout restart deploy/creatorclip-app deploy/creatorclip-worker`
```

### Step 3 — Re-encrypt every stored token (atomic)

```bash
docker compose -f docker-compose.prod.yml exec app \
  python3 scripts/rotate_token_key.py
```

The script prompts (hidden input) for the current and new keys — paste `<OLD_KEY>` then
`<NEW_KEY>`. Keys are never passed on argv (visible in `ps` / shell history); for
non-interactive runs export `OLD_TOKEN_ENCRYPTION_KEY` / `NEW_TOKEN_ENCRYPTION_KEY` in the
container environment instead.

The script re-encrypts every `youtube_tokens` row old→new in one transaction and prints a
final `Done. N rows re-encrypted, 0 errors.` If any row fails it rolls the whole transaction
back and exits non-zero — **do not change the primary key; investigate.**

### Step 4 — Promote the new key

Set `TOKEN_ENCRYPTION_KEY=<NEW_KEY>` and **clear** `TOKEN_ENCRYPTION_KEY_PREVIOUS=` (so a
leaked old key can no longer decrypt anything), then restart `app worker beat`.

**Re-escrow (Issue 255 — do not skip).** A rotated key makes the off-box escrow copies stale,
so a disaster restore would recover the DB but not be able to decrypt it. Immediately update
**both** escrow legs (GCP Secret Manager + password manager) with `<NEW_KEY>` and the new
`/opt/autoclip/.env` snapshot. See **Disaster Recovery → (a) Key loss** below.

### Step 5 — Verify

Sign in / trigger a YouTube refresh for one creator; confirm a clean `decrypt()` (no
`TokenDecryptError` in logs). The old key can now be destroyed.

### Rollback

- **Before step 4:** if the re-encrypt errors it already rolled itself back — leave the key
  unchanged and keep `TOKEN_ENCRYPTION_KEY_PREVIOUS` as-is.
- **After step 4:** set `TOKEN_ENCRYPTION_KEY` back to `OLD_KEY` and re-run step 3 in reverse
  (`--old-key NEW --new-key OLD`), or restore the pre-rotation DB backup.

---

## JWT_SECRET_KEY Rotation

JWT sessions are stateless — rotating `JWT_SECRET_KEY` immediately invalidates all active
sessions (creators are logged out on next request).

### Steps

1. Generate a new secret: `python3 -c "import secrets; print(secrets.token_hex(32))"`
2. Update `JWT_SECRET_KEY` in the secrets store
3. Restart the app — all existing `cc_session` cookies become invalid; creators re-auth via OAuth
4. No database migration needed

There is no gradual transition option for JWTs without adding a key-id header to tokens.
For zero-logout rotation, maintain both old and new keys temporarily in a `try/except` decode
chain in `auth.py` before removing the old key after one session expiry window
(`JWT_EXPIRY_MINUTES`).

---

## Money Refund (Issue 208)

When a paying creator requests a money refund, follow these two steps. **Never mutate the
original `MinutePack` row** — the ledger is immutable (same convention as automatic ingest-
failure refunds in `billing/refund.py`). The correction is a new compensating row.

### Why two steps?

Step 1 returns the money to the creator's payment method via Stripe. Step 2 records the
compensating credit in the CreatorClip ledger so the creator's `minutes_balance` is corrected
and the internal books stay in sync. Both steps are required; doing only Step 1 leaves the
ledger overstating available minutes.

### Full refund

**Step 1 — Issue the Stripe refund**

1. Go to [dashboard.stripe.com/payments](https://dashboard.stripe.com/payments).
2. Find the payment by the creator's Stripe customer ID or the original session/payment-intent.
3. Click **Refund** → choose **Full refund** → confirm.
4. Note the `stripe_session_id` from the original Checkout session (format: `cs_...`).

**Step 2 — Record the compensating ledger entry**

Run the following Python snippet (e.g. via `docker compose exec app python3 -c "..."` or a
psql call). Replace `CREATOR_UUID`, `MINUTES_TO_REVERSE` (the original pack's `minutes`), and
`STRIPE_SESSION_ID` with the actual values.

```python
import asyncio, uuid
import db
from billing.ledger import grant_minutes

async def _refund():
    creator_id = uuid.UUID("CREATOR_UUID")
    stripe_session_id = "cs_ORIGINAL_SESSION_ID"
    minutes_to_reverse = -MINUTES_TO_REVERSE  # negative to deduct from balance

    async with db.AdminSessionLocal() as session:
        await grant_minutes(
            creator_id=creator_id,
            minutes=minutes_to_reverse,
            reason="money_refund",
            session=session,
            pack_id=f"money_refund:{stripe_session_id}",
            price_cents=0,
        )
        await session.commit()
    print(f"Ledger corrected: {minutes_to_reverse} minutes applied to {creator_id}")

asyncio.run(_refund())
```

**Important notes:**

- `pack_id=f"money_refund:{stripe_session_id}"` uses a distinct namespace from ingest-failure
  refunds (`refund:{video_id}`). There is no UNIQUE index on this namespace, so do not run this
  script twice for the same session — check the ledger first (see Verify below).
- Negative minutes are allowed in the ledger for a full audit trail. The balance may go
  negative if minutes were already spent; that is the correct representation. The UI display
  layer clamps at 0 for the creator-facing balance.
- Do NOT mutate the original `MinutePack` row — immutable ledger invariant.

### Partial refund

**Step 1 — Issue the Stripe partial refund**

1. Follow Step 1 above but choose **Partial refund** and enter the refund amount in USD.
2. Note the original `stripe_session_id` and the `minutes` value proportional to the refund
   (e.g. if refunding 50% of a 2,000-minute pack, the compensating entry is -1,000 minutes).

**Step 2 — Record a proportional compensating entry**

Same as the full-refund script above, but use the proportional negative minutes value.

### Verify

Before running the script, confirm the ledger does not already have a `money_refund` entry
for this session:

```sql
SELECT id, pack_id, minutes_granted, reason, granted_at
FROM minute_packs
WHERE pack_id = 'money_refund:cs_ORIGINAL_SESSION_ID';
```

After running the script, confirm the new row exists and the creator's balance is correct:

```sql
SELECT minutes_balance FROM creators WHERE id = 'CREATOR_UUID';
SELECT pack_id, minutes_granted, reason, granted_at FROM minute_packs
WHERE creator_id = 'CREATOR_UUID' ORDER BY granted_at DESC LIMIT 5;
## Beat HA — RedBeat Recovery (Issue 263)

### Background

Celery Beat uses **RedBeat** (`celery-redbeat==2.3.3`) as its scheduler backend. Beat stores
the schedule and a distributed lock (`redbeat::lock`, TTL 1500s) in Redis. Beat runs as 1
replica with a Recreate strategy; the liveness probe restarts the pod if the heartbeat file
(`/tmp/celerybeat-schedule`) has not been updated in >300s.

The 30-day YouTube ToS purge (`purge_stale_youtube_analytics`, 6:00 UTC daily) is a compliance
obligation — a silent beat outage is a ToS risk, not just an operational issue.

### Symptoms of a beat outage

- `purge_stale_youtube_analytics`, `purge_stale_source_media`, `poll_clip_outcomes` stop
  appearing in Celery Flower / task logs.
- `kubectl get pod -l app.kubernetes.io/component=beat` shows a CrashLoopBackOff or the
  beat pod has been restarting at the liveness probe interval.

### Recovery steps

1. **Check beat pod status:**
   ```bash
   kubectl get pod -l app.kubernetes.io/component=beat
   kubectl logs -l app.kubernetes.io/component=beat --tail=100
   ```

2. **Confirm RedBeat schedule in Redis (optional diagnostic):**
   ```bash
   redis-cli -u "$REDIS_URL" keys "redbeat::*"
   ```
   You should see `redbeat::lock` (if a beat is running) and one key per schedule entry.

3. **Force a pod restart** (kubelet will pick up the Recreate rolling restart):
   ```bash
   kubectl rollout restart deployment/creatorclip-beat
   ```

4. **Verify liveness probe is passing:**
   ```bash
   kubectl describe pod -l app.kubernetes.io/component=beat | grep -A10 Liveness
   ```
   The probe checks that `stat -c %Y /tmp/celerybeat-schedule` is within 300s.

5. **If Redis is the SPOF:** a full Redis outage causes beat to fail at startup (cannot acquire
   lock). Restore the managed HA Redis instance first; beat will recover automatically on
   restart once Redis is available.

### After restoring beat — verify compliance tasks ran

```bash
# Confirm the ToS purge ran since the outage (look for purge_stale_youtube_analytics):
kubectl logs -l app.kubernetes.io/component=worker --since=24h | grep purge_stale_youtube
```

If the purge window was missed, trigger it manually:
```bash
kubectl exec -it deployment/creatorclip-worker -- \
  celery -A worker.celery_app call worker.tasks.purge_stale_youtube_analytics
```

---

## Personal Data Breach Response (GDPR Art. 33 / Art. 34)

**When to run:** On any confirmed or suspected personal data breach — unauthorised
access, disclosure, alteration, or destruction of personal data. Also run for
significant near-misses (e.g. a misconfigured storage bucket that was corrected before
external access is confirmed but cannot be ruled out).

**Legal deadline:** GDPR Art. 33(1) requires notification to the competent supervisory
authority **within 72 hours of becoming aware** of a breach that is likely to result in
a risk to the rights and freedoms of natural persons. The 72-hour clock starts at
"awareness" — the point when there is a reasonable degree of certainty that a breach
has occurred (not when all details are known). Phased reporting is permitted under
Art. 33(4): file an initial notification with available information and supplement it
as more facts are established.

**Breach records must be retained for at least 3 years** per Art. 33(5) as evidence.

**Named owner / escalation contact:**
- Primary: reesepludwick@gmail.com (owner until a DPO is designated)
- DPO: _[PLACEHOLDER — must be filled with a real person before production launch]_
- Legal counsel: _[PLACEHOLDER — fill before production launch]_

> **Human action required:** Both placeholder fields above must be replaced with real
> contacts before this runbook is authorised for production use.

---

### Step 1 — Detection and triage (0–2 hours)

Potential breach sources to monitor:
- Log anomalies: unexpected data egress, bulk query patterns, admin-action spikes
- Vendor notification: breach notice from Anthropic, Voyage AI, Deepgram, Cloudflare R2,
  Stripe, or Google (see SUBPROCESSORS.md for each vendor's breach-notify expectation —
  typically 24–48 hours from their discovery)
- Security alerts: WAF, IDS, GCP Security Command Center
- User report: creator reports unexpected access to their data

Gather the following before moving to risk assessment:

| Field | Notes |
|-------|-------|
| Date and time of discovery | |
| Nature of breach | Confidentiality / Integrity / Availability |
| Affected data categories | OAuth tokens, email, analytics, audio, billing |
| Approximate count of affected data subjects | |
| Approximate count of affected records | |
| Likely cause | |
| Is breach ongoing? | Yes / No / Unknown |
| Immediate containment actions taken | |

---

### Step 2 — Risk assessment (2–4 hours)

Determine whether the breach is "likely to result in a risk to the rights and freedoms
of natural persons" (Art. 33(1)).

**Low-risk indicators (no supervisory authority notification required):**
- Data is already publicly available
- Data is strongly encrypted and the key is not compromised
- Breach is an internal error corrected before any external access

**Document the low-risk rationale** in the breach log (Step 5) and stop here if the
risk assessment is negative.

**High-risk indicators (Art. 34 individual notification also required):**
- OAuth token exposure (high-risk: enables account takeover)
- Audio content exfiltration (high-risk: private creator content)
- Financial data compromise via Stripe (high-risk: even though AutoClip never holds
  raw card numbers, a Stripe account breach may require individual notification)
- Bulk PII exposure (email addresses, demographic inferences)

---

### Step 3 — Supervisory authority notification (within 72 hours of awareness)

**Only required if risk assessment in Step 2 concludes risk exists.**

GDPR Art. 33(3) required fields for the notification:

1. **Nature of the personal data breach** — including categories and approximate numbers
   of data subjects and records concerned.
2. **Contact point** — name and contact details of the DPO or other contact point.
3. **Likely consequences** — describe probable consequences of the breach.
4. **Measures taken or proposed** — including measures to mitigate possible adverse effects.

File the notification via the supervisory authority's online portal. For EU creators,
the lead supervisory authority depends on the country where AutoClip's EU establishment
is located (or the country of the majority of affected data subjects if there is no EU
establishment). For UK creators, notify the ICO (ico.org.uk).

Phased reporting (Art. 33(4)): if all information is not yet available, submit an
initial notification with available facts and state that a supplement will follow.
Supplements must be filed "without undue delay."

---

### Step 4 — Sub-processor notify chain

Reference `docs/SUBPROCESSORS.md` for each vendor's breach-notify obligation. Each
vendor's DPA typically requires the data processor (AutoClip) to notify them within
24–48 hours of discovery of a breach involving their services, and vice versa.

**Key actions:**
- If the breach originated from a sub-processor: confirm their breach notification to
  AutoClip, gather Art. 33(3) fields from them, and file the supervisory authority
  notification on behalf of affected creators.
- If the breach is on AutoClip's infrastructure: notify each affected sub-processor per
  their DPA breach-notify clause.

---

### Step 5 — Art. 34 high-risk individual notification

**Only required** if the breach is "likely to result in high risk to the rights and
freedoms of natural persons" (Art. 34(1)).

Notify affected data subjects "without undue delay" using clear and plain language.

**Sample data-subject notice template:**

> Subject: Important notice about your AutoClip data
>
> We are writing to inform you of a personal data incident that may affect your
> AutoClip account.
>
> **What happened:** [brief description]
>
> **What data was affected:** [categories — e.g. email address, YouTube channel ID]
>
> **What we have done:** [containment and remediation steps taken]
>
> **What you should do:** [recommended action — e.g. revoke AutoClip access via
> Google Security Settings at security.google.com/settings/security/permissions,
> change your Google account password if OAuth tokens were compromised]
>
> **Contact:** reesepludwick@gmail.com — we will respond within 5 business days.

---

### Step 6 — Post-incident documentation

Retain the breach record for at least 3 years per Art. 33(5):

- Completed triage table (Step 1)
- Risk assessment rationale (Step 2)
- Copy of supervisory authority notification (if filed) + any supplements
- Copy of sub-processor notifications (if applicable)
- Copy of individual notices sent (if Art. 34 applied)
- Timeline: detection → awareness → notification → containment → remediation
- Lessons learned and process improvements

Store breach records in a secure, access-controlled location (not in the application
database — use a separate encrypted document store or password manager).

---

### Sources

- GDPR Art. 33: https://gdpr-info.eu/art-33-gdpr/
- GDPR Art. 34: https://gdpr-info.eu/art-34-gdpr/
- EDPB guidelines on breach notification (WP250 rev.01)
- docs/SUBPROCESSORS.md — vendor breach-notify chain and DPA details
- docs/COMPLIANCE.md — data classes and retention policy

---

## Disaster Recovery (Issues 255–258)

The live app runs on a single VM (`/opt/autoclip`, docker-compose) with one `postgres`
container and R2 object storage. This section covers the four data-loss failure modes and
their recovery paths. **`[DEC]` rationale: `docs/DECISIONS.md` 2026-06-27 (DR batch).**

### One-time setup (operator — required to ACTIVATE these protections)

These are the external steps that arm the tooling shipped in this batch:

1. **Key escrow (Issue 255) — do this FIRST.** Copy three irreplaceable values off-box to
   **two independent legs**: (1) a personal password manager (1Password/Bitwarden) and
   (2) GCP Secret Manager:
   - `TOKEN_ENCRYPTION_KEY` (Fernet — authenticated encryption, no recovery path if lost)
   - `JWT_SECRET_KEY`
   - a snapshot of `/opt/autoclip/.env`
   ⚠️ **Never** commit these to git/CI logs, and **never** store them inside the DB backup
   they protect (circular dependency). Re-escrow after every key rotation (see that runbook).
2. **Backup bucket (Issue 256).** Create a **separate** R2 bucket `creatorclip-backups`
   (distinct from the media `R2_BUCKET`). Set `BACKUP_R2_BUCKET`, `BACKUP_ENCRYPTION_KEY`
   (a strong passphrase, escrowed off-box — NOT inside the backup) in the VM `.env` and as
   GitHub Actions secrets. Install `awscli` on the VM.
3. **R2 immutability (Issue 258).** On `creatorclip-backups` enable an **Object Lock in
   Compliance mode** (≥14d) so a compromised VM credential cannot delete backups
   (Governance mode is admin-overridable — do not use it). Add R2 **Lifecycle** rules:
   `daily/` expires at `BACKUP_RETENTION_DAILY` days, `weekly/` at ~56d, `predeploy/` short.
   On the **media** bucket: a short Object Lock on `clips/` and a lifecycle on `source/`
   matching `SOURCE_MEDIA_RETENTION_HOURS`. Reconcile lock windows with right-to-erasure
   (Issue 254).
4. **Schedule (Issue 256).** Add the nightly cron on the VM:
   ```
   7 3 * * *  cd /opt/autoclip && ./scripts/backup_pg.sh >> /var/log/creatorclip-backup.log 2>&1
   ```
   Optionally set `BACKUP_HEALTHCHECK_URL` for dead-man's-switch alerting.

### (a) Key loss — the encryption key is gone

- **With escrow:** retrieve `TOKEN_ENCRYPTION_KEY` from either escrow leg, restore it to the
  VM `.env`, restart `app worker beat`. Stored tokens decrypt normally.
- **Without escrow (fallback):** the ciphertext in `youtube_tokens` is permanently
  unrecoverable. Clear the affected token columns and **force every creator to re-OAuth**
  (re-populates `youtube_tokens` under a new key). Billing/DNA/preference data is unaffected.

### (b) Database loss — disk/volume gone

1. Stand up a throwaway Postgres (or rebuild the VM `postgres` container).
2. Pull the latest dump from R2 and decrypt+restore:
   ```bash
   AWS_ACCESS_KEY_ID=$R2_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY=$R2_SECRET_ACCESS_KEY \
     aws s3 cp s3://creatorclip-backups/daily/<OBJECT>.sql.gz.enc - \
       --endpoint-url https://$R2_ACCOUNT_ID.r2.cloudflarestorage.com \
   | openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 -pass env:BACKUP_ENCRYPTION_KEY \
   | gunzip \
   | docker compose -f docker-compose.prod.yml exec -T postgres \
       sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
   ```
3. Restore `TOKEN_ENCRYPTION_KEY` from escrow (step a) — **the restore is useless without it.**
4. **MANDATORY — re-apply erasures (Issue 254).** The restored dump is older than the DB it
   replaces, so it can resurrect creators erased AFTER the dump was taken — a GDPR Art. 17
   violation if served. Before returning to service, run:
   ```bash
   python3 scripts/reapply_erasures.py
   ```
   Source erasures from the **NEWEST audit trail available** — the live `audit_log` if it
   survived, else the newest dump's — never only the restored (older) one. If the restored
   `audit_log` predates the newest dump, first re-insert the newer `creator.deleted` rows
   from that dump, then run the script. It is idempotent (already-absent creators are
   skipped), so re-running is always safe.

#### Restore drill (quarterly — "an untested backup is not a backup")

Run against a throwaway target, record the result + measured RTO:
- [ ] `/health` returns `ok`
- [ ] one creator's token `decrypt()`s with no `TokenDecryptError`
- [ ] precious-table row counts match expectation (`preference_models`, `clip_outcomes`,
      `creator_dna`, billing ledgers)
- [ ] measured **RTO** recorded here: ________

### (c) R2 data lost / wrongly deleted

- **Within the Object Lock (Compliance) window:** the object was never deletable — restore by
  re-fetching it; investigate the erroneous `delete_prefix` (`worker/storage.py`).
- **Outside the window:** R2 has no GA versioning, so a deleted render is gone. Re-render from
  source **only if** the source is still within its 72h retention; otherwise it is unrecoverable.

### (d) Bad migration

- A pre-migration dump is taken automatically before `alembic upgrade head` in both deploy
  paths (Issue 257), under the `predeploy/` prefix. To roll back a bad schema change, restore
  the most recent `predeploy/` dump using the procedure in **(b)**, then redeploy the prior
  image (see the rollback note in `deploy.yml` / Issue 271).
- **MANDATORY after the restore:** run `python3 scripts/reapply_erasures.py` (step 4 in
  **(b)**), sourcing erasures from the NEWEST audit trail — any account deleted between the
  `predeploy/` dump and the rollback would otherwise be resurrected (Issue 254).

### Sources

- PostgreSQL backup & restore: https://www.postgresql.org/docs/current/backup.html
- Cloudflare R2 Object Lock: https://developers.cloudflare.com/r2/buckets/object-lock/
- 3-2-1 backup rule (CISA): https://www.cisa.gov/

---

## Redis broker durability & recovery (Issue 288)

Prod Redis (`docker-compose.prod.yml`) runs `--appendonly yes --appendfsync everysec
--save 300 100` on the named volume `redis_data`. Celery is at-least-once
(`task_acks_late=True`, `task_reject_on_worker_lost=True`, `visibility_timeout=3600`).
Staging Redis is **intentionally ephemeral** (`--save '' --appendonly no`) — do not "fix" it.

**Loss windows (know these before an incident):**

| Event | What's lost |
|---|---|
| Redis crash / container restart | ≤1s of enqueues (AOF everysec fsync window) |
| Forcible worker kill (SIGKILL) | Nothing — unacked tasks are redelivered after the 3600s `visibility_timeout` elapses (i.e. up to an hour of apparent stall, not loss) |
| `redis_data` volume destroyed | Entire queue + Beat schedule state. Recover via re-enqueue (below); the nightly `scripts/backup_redis.sh` snapshot is belt-and-suspenders only — it is minutes-to-hours stale by nature |

**Recovery — the DB is the source of truth, not the broker.** Pipeline tasks are idempotent
and their progress lives in DB status rows. After any queue loss:

1. Restart the stack: `docker compose -f docker-compose.prod.yml up -d redis worker beat`.
2. Re-enqueue stuck work from DB state — videos parked in a non-terminal status with no
   running task: use `scripts/clip_pipeline_state.py` to list them, then re-queue via the
   normal API/queue endpoints (idempotency markers make re-runs safe).
3. Beat (RedBeat) rebuilds its schedule from code on startup — see "Beat HA — RedBeat
   Recovery" above; periodic sweeps self-heal on their next tick.

**Backup schedule (operator):** add alongside the PG cron:
```
27 3 * * *  cd /opt/autoclip && ./scripts/backup_redis.sh >> /var/log/creatorclip-backup.log 2>&1
```
Requires the same `BACKUP_*`/R2 env as `backup_pg.sh` (one-time setup step 2 above).

**Restart drill (do once after applying the compose change, record the result):** with the
worker paused (`docker compose stop worker`), enqueue N test tasks →
`docker compose restart redis` → `docker compose start worker` → assert all N execute.
Result + date: ________

Sources: https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/ ·
https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html
(accessed 2026-07-02)

---

## Monthly Cost Review (Issue 292)

**When:** first week of each month, ~15 minutes. **Goal:** reconcile the three COGS
lines — (1) metered usage in our own ledger, (2) the DigitalOcean invoice, (3)
Cloudflare R2 storage — and catch drift or a per-creator outlier before it compounds.

### 1 — Usage ledger (LLM + transcription cost estimates)

Run against prod Postgres (read-only). Monthly totals per period:

```sql
SELECT period,
       SUM(cost_estimate)          AS est_cost_usd,
       SUM(tokens_in + tokens_out) AS total_tokens
FROM usage
GROUP BY period
ORDER BY period DESC;
```

Then the per-creator isolation sanity check — the top 5 creators by cost for the
latest period. One creator dominating far beyond their plan tier means a quota or
isolation bug, not a big user:

```sql
SELECT creator_id,
       SUM(cost_estimate)          AS est_cost_usd,
       SUM(tokens_in + tokens_out) AS total_tokens
FROM usage
WHERE period = to_char(now() - interval '1 month', 'YYYY-MM')
GROUP BY creator_id
ORDER BY est_cost_usd DESC
LIMIT 5;
```

Estimates use the price book in `config.py` (`PRICE_BOOK_VERSION` stamps the rates);
a step-change month-over-month with flat usage usually means the price book changed
— check the version stamp before suspecting a leak.

The Grafana panel `docs/dashboards/llm-cost-panel.json` shows the same spend as a
daily-rate time series — `sum by (provider) (increase(llm_cost_usd_total[1d]))` —
for eyeballing trend between reviews (the `llm_cost_usd_total` counter ships with
Issues 290/291).

### 2 — DigitalOcean invoice (compute)

DO console → Billing → most recent invoice. Compare the VM + bandwidth total to last
month; anything >20% up without a matching usage-ledger increase is a SEV3 to
investigate (orphaned droplet, snapshot pile-up, egress spike).

### 3 — Cloudflare R2 (storage)

Cloudflare dashboard → R2 → bucket → **Metrics** tab: storage bytes + Class A/B
operations for the month. Cross-check against the `r2_bytes_stored{prefix}` /
`r2_objects{prefix}` gauges (daily Beat sweep, Issue 293): the `source/` prefix
should stay bounded by the ToS retention purge — steady growth there means the purge
is not running (see Beat HA runbook). `clips/` + `summaries/` growth should track
creator activity.

**Record the result** (date, three totals, any action filed) in the ops scratch log;
promote anomalies to `docs/issues.md`.
---

## Spend guard trip & reset (Issues 290+291)

`billing/spend_guard.py` enforces USD caps on LLM spend from Redis microdollar
counters incremented at the billing-ledger choke point (`record_llm_usage`, plus
chat's `increment_usage` path). Caps and the warn ratio live in `.env`
(`SPEND_CAP_*`, `SPEND_WARN_RATIO`, `SPEND_VELOCITY_*`, `SPEND_COOLDOWN_TTL_S`).
**Everything here resets manually — nothing un-trips on its own except by TTL.**

### What a trip looks like

| Breach | Effect | Signal |
|---|---|---|
| ≥80% of any cap | Warning only — nothing blocked | `spend_cap_warning` event (log + event_log row), once per window |
| 100% per-creator daily cap or creator velocity | That creator gets 429s on LLM/render routes + their paid tasks stop; cool-down key TTL `SPEND_COOLDOWN_TTL_S` (default 1h) | `spend_cap_tripped` event, `scope=creator` |
| 100% global daily / monthly cap, or global velocity (rolling ~15 min) | `llm_generation` kill switch flipped OFF for everyone (`updated_by=spend_guard`) behind a trip-latch | `spend_cap_tripped` event, `scope=global` + `flag_flipped` event |

The guard **fails open** on Redis errors: caps stop being enforced (warn-once log
line), LLM features keep working.

### Diagnose

```bash
# Which flag state are we in?
python3 scripts/flags.py list

# What tripped? (events carry cap name + spent/cap USD)
docker compose -f docker-compose.prod.yml logs app worker | grep -E 'spend_cap_(warning|tripped)'

# Current counters (values are MICRODOLLARS — divide by 1,000,000 for USD)
redis-cli --scan --pattern 'creatorclip:spend:*'
redis-cli GET "creatorclip:spend:$(date -u +%F)"                # global daily
redis-cli GET "creatorclip:spend:$(date -u +%Y-%m)"             # global monthly
redis-cli GET "creatorclip:spend:$(date -u +%F):creator:<id>"   # one creator
```

### Reset — global trip (llm_generation off)

Investigate FIRST (runaway loop? abuse? legitimately busy day?). Then:

```bash
# 1. Clear the trip-latch so a still-breached counter can re-trip cleanly
redis-cli DEL creatorclip:spend:trip:llm_generation

# 2. If the underlying counter is still over the cap, either raise the cap in
#    .env (SPEND_CAP_GLOBAL_DAILY_USD / _MONTHLY_USD) + restart app & worker,
#    or delete the counter to forgive the window:
redis-cli DEL "creatorclip:spend:$(date -u +%F)"        # forgive today (rare)

# 3. Re-enable the feature
python3 scripts/flags.py enable llm_generation --reason "spend trip investigated: <why>"
```

If you re-enable without step 1/2, the very next billed call re-trips the breaker —
that is by design.

### Reset — single creator cool-down

```bash
redis-cli DEL "creatorclip:spend:cooldown:creator:<creator-id>"
# If their daily counter is still at the cap they will re-enter cool-down on the
# next billed call; to actually unblock, also delete their daily counter or raise
# SPEND_CAP_CREATOR_DAILY_USD:
redis-cli DEL "creatorclip:spend:$(date -u +%F):creator:<creator-id>"
```

Otherwise the cool-down simply expires after `SPEND_COOLDOWN_TTL_S` (default 1h),
and daily counters roll over at midnight UTC.

### Verify

- `python3 scripts/flags.py list` shows `llm_generation` enabled.
- A cheap LLM route (e.g. clip title suggestions) returns 202/200, not 429/503.
- Grafana: `llm_cost_usd_total` (by provider/model) resumes climbing at a sane slope.
