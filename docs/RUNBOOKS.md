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

### Pre-flight checklist

- [ ] You have read access to the production database
- [ ] You have write access to the production secrets store (`.env`, Kubernetes Secret, etc.)
- [ ] A maintenance window is scheduled (rotation causes a brief ~seconds window where
      old and new ciphertexts co-exist — `MultiFernet` handles this transparently)
- [ ] A database backup has been taken

---

### Step 1 — Generate a new key

```bash
python3 -c "from crypto import generate_key; print(generate_key())"
```

Copy the output. It looks like: `p7k3R8...=` (44 base64 chars). Call it `NEW_KEY`.

---

### Step 2 — Re-encrypt all tokens

Run the re-encryption script. It accepts the old key and new key, re-encrypts every row
in `youtube_tokens`, and commits atomically.

```bash
python3 scripts/rotate_token_key.py \
  --old-key "$OLD_TOKEN_ENCRYPTION_KEY" \
  --new-key "$NEW_KEY"
```

The script prints a progress line for every creator row and a final summary:

```
Re-encrypting tokens for 42 creator(s)...
  [1/42] creator=<uuid> ok
  ...
  [42/42] creator=<uuid> ok
Done. 42 rows re-encrypted, 0 errors.
```

If any row fails, the script rolls back the entire transaction and exits non-zero.
**Do not update the key in the environment until this step reports 0 errors.**

---

### Step 3 — Update the secret

Replace `TOKEN_ENCRYPTION_KEY` in your secrets store with `NEW_KEY`:

**Docker Compose / `.env` file:**
```
TOKEN_ENCRYPTION_KEY=<NEW_KEY>
```

**Kubernetes Secret:**
```bash
kubectl create secret generic creatorclip-secrets \
  --from-literal=TOKEN_ENCRYPTION_KEY=<NEW_KEY> \
  --dry-run=client -o yaml | kubectl apply -f -
```

---

### Step 4 — Restart the app

```bash
# Docker Compose
docker compose restart app worker

# Kubernetes
kubectl rollout restart deployment/creatorclip-app deployment/creatorclip-worker
kubectl rollout status deployment/creatorclip-app
```

---

### Step 5 — Smoke test

```bash
# A protected endpoint should return 200 (token decrypt works with new key)
curl -s -b "cc_session=<valid_jwt>" https://<host>/auth/me | jq .
```

---

### Rollback

If Step 4 fails (bad key format, missed a row):

1. Restore the database from the pre-rotation backup
2. Re-deploy with the original `TOKEN_ENCRYPTION_KEY`
3. Investigate the script error before retrying

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
