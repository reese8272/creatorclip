# Runbook — Disaster Recovery & Durability (Issues 255, 258)

> Hand-drafted W0 runbook. Both are `external` (live VM + Cloudflare R2 / GCP consoles). Only the
> doc/DECISIONS edits are reviewable on the dev box. **Do Issue 255 FIRST** — every other DR issue
> (esp. 256 backup/restore) is worthless if the encryption key isn't escrowed.

---

## Issue 255 — Off-box escrow of the irreplaceable secrets

**Why first:** `TOKEN_ENCRYPTION_KEY`, `JWT_SECRET_KEY` and every provider secret exist in exactly one
place — `/opt/autoclip/.env` (chmod 600) on the single VM. If that disk dies, the Fernet key is gone and
**every** `access_token_encrypted` / `refresh_token_encrypted` in `youtube_tokens` becomes permanently
undecryptable (authenticated encryption — no recovery). A perfect Postgres restore then yields useless ciphertext.

**Steps (out-of-band, never via git/CI):**
1. Copy these three off-box into **two independent** locations:
   - a personal password manager (1Password / Bitwarden), AND
   - **GCP Secret Manager** (the already-chosen prod secrets backend per `docs/DEPLOYMENT.md:45` —
     adopting it now de-risks the eventual K8s migration).
   Secrets: `TOKEN_ENCRYPTION_KEY`, `JWT_SECRET_KEY`, and a snapshot of `/opt/autoclip/.env`.
2. Edit `docs/RUNBOOKS.md`: add a **"re-escrow after promotion"** step to the `TOKEN_ENCRYPTION_KEY`
   Rotation section (Step 4) so escrow can't silently go stale after a rotation.
3. Edit `docs/RUNBOOKS.md`: add a **"Disaster Recovery → key loss"** section documenting (a) restore from
   escrow, and (b) the no-escrow fallback = force every creator to re-OAuth (repopulates `youtube_tokens` under a new key).
4. Edit `docs/SECRETS.md` (VM `.env` row): document the escrow location + recovery procedure.
5. `docs/DECISIONS.md`: record `[DEC]` — GCP Secret Manager adopted as the escrow backend (cite `docs/DEPLOYMENT.md:45`).

**Done when:**
- [ ] The three secrets are retrievable from **both** escrow legs (verify before closing)
- [ ] Neither escrow copy appears in git, any CI log, or any backup-tool log
- [ ] RUNBOOKS.md has the re-escrow step + the "key loss" DR entry; DECISIONS.md records the backend
- [ ] Keep the password-manager leg independent — don't rely on Secret Manager alone (GCP billing/project loss is its own failure mode)

---

## Issue 258 — R2 durability hardening (Bucket Lock + lifecycle)

**Why:** R2 has 11-nines hardware durability but **no GA object versioning** — an accidental/malicious
delete is unrecoverable. `worker/storage.py:78 delete_prefix` is unfiltered by design and runs on erasure
for `source/{creator_id}/` and `clips/{creator_id}/`; a bad prefix could wipe undelivered renders.
**R2-config-only — do NOT add filtering to `delete_prefix` (that's off-course scope).**

**Steps (Cloudflare R2 dashboard / API):**
1. Enable an **R2 Bucket Lock** (short retention, e.g. a few days) on the **`clips/`** prefix so a bad
   `delete_prefix` can't wipe recently-rendered, undelivered clips inside the window.
2. Add an **R2 Object Lifecycle** rule on the **`source/`** prefix expiring objects in line with
   `SOURCE_MEDIA_RETENTION_HOURS` (default 72h) — belt-and-suspenders behind the hourly
   `purge_stale_source_media` beat (`worker/schedule.py:30`).
3. **Reconcile with right-to-erasure (Issue 254):** the lock window on `clips/` must not block defensible
   deletion, and must not accidentally pin `source/` past its 72h ToS purge (Bucket Lock takes precedence over lifecycle).
4. `docs/DECISIONS.md`: `[DEC]` — R2 has no GA versioning → Bucket Locks chosen as the delete-protection lever (+ Cloudflare evidence link).
5. `docs/COMPLIANCE.md`: document the `source/` lifecycle rule and the `clips/` lock window in the retention table.
6. `docs/RUNBOOKS.md` (DR section, shared with 255): add "R2 data lost / wrongly deleted" — inside the lock window objects were never deletable; outside it, re-render only if source still within 72h.

**Done when:**
- [ ] Bucket Lock active on `clips/`; a test delete within the window is **rejected**
- [ ] Lifecycle rule expires `source/` objects per `SOURCE_MEDIA_RETENTION_HOURS`
- [ ] Lock window reconciled with Issue 254 erasure stance; DECISIONS + COMPLIANCE updated
