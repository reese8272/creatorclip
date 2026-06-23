# Runbook — Publish to YouTube: `youtube.upload` scope (Issue 194)

> Hand-drafted W0 runbook. Verify type: `external`. **Important:** the *code* for this is already
> implemented on the held branch **`feat/batch-b-publish`** (not on main). The engineering work is to
> **land + verify** it; the gating work is the external **Google OAuth re-verification** (Issue 29).
> The scope-string assembly + predicate are unit-testable locally; the actual grant needs the live consent screen.

---

## What it does

Adds the **sensitive** write scope `https://www.googleapis.com/auth/youtube.upload` via **incremental
consent** (`include_granted_scopes=true`) so existing read-only creators opt in **without** a forced
re-auth, and read-only creators' flow is unchanged.

## Code landing (from `feat/batch-b-publish`)

- `youtube/oauth.py` — add `PUBLISH_SCOPE` constant, `has_publish_scope(scope)` predicate, and an
  `include_publish=True` variant on `build_authorization_url` (with `include_granted_scopes`). Keep base `SCOPES` read-only.
- `routers/auth.py` — add `GET /auth/connect-publishing` (write-scope consent, opt-in only); add `can_publish`
  to `AuthMeOut` / `/auth/me` (derived from the stored token scope).
- `frontend/src/components/profile/PublishingSection.tsx` (new) + mount in `Profile.tsx`; add `can_publish` to `types.ts`.
- `docs/COMPLIANCE.md` — flip the `youtube.upload` row from "deferred to Phase 2" → requested-on-opt-in (minimum-necessary).
- `docs/DECISIONS.md` — merge the §6 umbrella scope-expansion entry.
- **Reconcile with main** since the branch point (auth.py `/me`, COMPLIANCE.md may have shifted).

## External gate (the real blocker) — Issue 29

`youtube.upload` is a **SENSITIVE** scope, not restricted → **no** paid third-party CASA assessment.
Verification = Google OAuth app verification (Issue 29): a **demo video** showing the end-to-end publish
flow + the consent screen with the EXACT scopes, plus a written justification that no narrower scope
publishes a video. The separate **YouTube API compliance audit** (branding/privacy) rides with the
**quota-extension** request (Issue 260), *not* with adding this scope. Until verification clears,
**Issue 195 forces `videos.insert` to `private`** — honest day-one UX is private upload + manual publish.

**Done when:**
- [ ] `youtube.upload` requested **only** for opt-in creators; read-only flow unchanged
- [ ] Incremental consent layers the write scope without dropping read-only access (verify the merged grant)
- [ ] `/auth/me` returns `can_publish` from `has_publish_scope`; tokens stay Fernet-encrypted, never logged
- [ ] COMPLIANCE scope table + DECISIONS umbrella updated
- [ ] Google OAuth re-verification (Issue 29) tracked as the launch dependency (multi-day external gate)
