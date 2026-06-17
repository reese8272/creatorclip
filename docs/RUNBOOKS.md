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
