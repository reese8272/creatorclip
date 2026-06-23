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
- [ ] A database backup has been taken (belt-and-suspenders; the re-encrypt is atomic)

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
docker compose -f docker-compose.prod.yml exec -T app \
  python3 scripts/rotate_token_key.py --old-key "<OLD_KEY>" --new-key "<NEW_KEY>"
```

The script re-encrypts every `youtube_tokens` row old→new in one transaction and prints a
final `Done. N rows re-encrypted, 0 errors.` If any row fails it rolls the whole transaction
back and exits non-zero — **do not change the primary key; investigate.**

### Step 4 — Promote the new key

Set `TOKEN_ENCRYPTION_KEY=<NEW_KEY>` and **clear** `TOKEN_ENCRYPTION_KEY_PREVIOUS=` (so a
leaked old key can no longer decrypt anything), then restart `app worker beat`.

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
