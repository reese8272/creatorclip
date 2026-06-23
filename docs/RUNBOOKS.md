# CreatorClip — Operations Runbooks

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
```
