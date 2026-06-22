# Research Brief — Data Privacy & Compliance (GDPR / CCPA, Erasure & Export)

> **Issue 177.** Read-only research / planning brief. Engineering + policy gap analysis —
> **not legal advice; the author is not a lawyer.** Scope is **privacy law** (GDPR, UK GDPR,
> CCPA/CPRA) and the data-subject-rights machinery. YouTube API ToS is owned by
> `docs/COMPLIANCE.md` + research prompt 04 and is cross-referenced, not re-litigated here.
> Date: 2026-06-22. Industry standard researched first (the One Rule); every external claim
> links a source, every repo claim cites `file_path:line`.

---

## 1. Executive summary

CreatorClip's privacy posture is **good in intent** (PII minimization, encrypted tokens, RLS,
source-media purge, account deletion) but has **concrete, demonstrable gaps in the rights
machinery** that block a clean public launch and an honest Privacy Policy. Severity-tagged:

| # | Gap | Severity | One-line |
|---|-----|----------|----------|
| A | **Account deletion re-writes the deleted creator's `email` + `channel_id` into `audit_log`**, which is RLS-exempt and never purged | **SEV-1** | `routers/auth.py:268-275` — erasure re-introduces the very PII it just erased; survives forever |
| B | **`event_logs` rows survive account deletion** — `creator_id` is a bare column with **no FK / no CASCADE**, and the table is excluded from the delete path | **SEV-1** | `models.py:724` + `routers/auth.py:278` — telemetry tied to a deleted creator persists indefinitely |
| C | **No data-export / access endpoint** (GDPR Art. 15/20, CCPA "right to know") | **SEV-1** | Nothing in `routers/`; Privacy Policy promises only deletion, not access/portability |
| D | **No defined retention schedule** for tokens of churned users, `event_logs`, `audit_log`, or analytics-of-inactive-creators; `event_logs` retention is explicitly "TBD" | **SEV-2** | `docs/COMPLIANCE.md:87` — minimization + storage-limitation (Art. 5(1)(e)) unmet |
| E | **No DPAs on record with sub-processors; no Art. 30 Record of Processing; no sub-processor list page** | **SEV-2** | Anthropic/Voyage/Deepgram/R2/Stripe/Google all process creator PII; Art. 28 requires DPAs + flow-down |
| F | **Consent + transparency gaps**: no recorded/timestamped consent at sign-up (implicit "by signing in you agree" only); Privacy Policy omits sub-processors, audience-demographics third-party nature, international transfer, breach process, and a CCPA notice | **SEV-2** | `frontend/src/pages/Login.tsx:43`; `static/privacy.html` |
| G | **No breach-notification runbook** (GDPR Art. 33 72-hour duty; processor-notify chain) | **SEV-2** | Not in `docs/RUNBOOKS.md`; required before processing real EU/UK data |
| H | **R2 / backup erasure not proven**: `delete_prefix` covers `source/` + `clips/` but R2 object versioning / lifecycle and any DB backup retention are undocumented vs the "put beyond use" standard | **SEV-3** | `routers/auth.py:256-265`; needs a documented backup-erasure stance |

The two SEV-1 erasure leaks (A, B) and the missing export (C) are the headline items: they make
the current "we delete all your data" claim in `static/privacy.html:85` and `static/tos.html`
**factually inaccurate** today.

---

## 2. The data-processing record (inventory + map)

This is the Art. 30 starting point. Lawful basis is **proposed** (a counsel decision — see §5).
"Sub-processor" = where the data physically flows.

| Data element | Store (`file_path`) | Purpose | Proposed lawful basis | Retention today | Sub-processor |
|---|---|---|---|---|---|
| Google `sub`, `email`, `channel_id`, `channel_title` | `creators` (`models.py:114`) | Identity / account | Contract (Art. 6(1)(b)) | Until account delete | Google (auth) |
| OAuth access/refresh tokens (Fernet-encrypted) | `youtube_tokens` (`models.py:212`) | API access | Contract | Until delete/revoke; **no churn TTL** | Google |
| Per-video metrics, retention curves | `video_metrics`, `retention_curves` (`models.py:294,309`) | DNA / analytics | Contract | 30-day staleness purge (`worker/schedule.py:38`) | — |
| **Audience demographics** (age-group × gender %, aggregated) | `demographics.payload_jsonb` (`models.py:340`; `youtube/analytics.py:198-202`) | DNA / timing | Contract; **note: third-party (audience) data the creator shares** | 30-day staleness purge | Google |
| Audience activity windows | `audience_activity` (`models.py:328`) | Upload timing | Contract | 30-day staleness purge | Google |
| Uploaded source video bytes | R2 `source/{creator_id}/` (`worker/storage.py`) | Clip processing | Contract | `SOURCE_MEDIA_RETENTION_HOURS` (72h) purge | Cloudflare R2 |
| Rendered clips | R2 `clips/{creator_id}/` | Deliverable | Contract | Until delete | Cloudflare R2 |
| Transcripts (word-level, may contain spoken PII) | `transcripts` (`models.py:353`) | Clipping | Contract | Until video delete | **Deepgram/AssemblyAI** (if hosted backend) |
| Creator DNA, identity, embeddings | `creator_dna`, `creator_identity`, `dna_embeddings` | Core feature | Contract | Until delete | **Anthropic** (synthesis), **Voyage** (embeddings) |
| Clip feedback / outcomes | `clip_feedback`, `clip_outcomes` (`models.py:541,571`) | Preference model | Contract | Until delete | — |
| Chat conversations + messages | `chat_conversations`, `chat_messages` (`models.py:861,894`) | Pro chatbot | Contract | Until conv/account delete (CASCADE) | **Anthropic** |
| Billing ledger | `minute_packs`, `minute_deductions`, `usage` | Billing | Legal obligation (tax) / Contract | Until delete (**but tax law may require retention**) | **Stripe** |
| **Telemetry** (`http_request`, UI events; `creator_id`, path, status, request_id) | `event_logs` (`models.py:699`) | Beta analysis | **Legitimate interest (proposed)** | **TBD** (`docs/COMPLIANCE.md:87`) | — |
| **Audit trail** (incl. deleted creator's `email`+`channel_id`) | `audit_log` (`models.py:680`) | Security ops | Legal obligation / Legit. interest | **Forever** (append-only) | — |
| Task progress (creator_id as stream owner) | Redis (`worker/progress.py:197`) | Live SSE | Contract | TTL 3600s (`_OWNER_TTL_SECONDS`) — ephemeral, OK | — |

**Minimization read:** the inventory is lean and feature-justified — no obviously orphaned
collection. The one flag is that **`audit_log` deliberately stores PII (`email`, `channel_id`)
of the very creator being deleted** (gap A), which inverts minimization.

---

## 3. Rights-machinery findings

Each: **standard → repo reality → fix.**

### 3.1 Right to erasure (GDPR Art. 17 / CCPA §1798.105)

**Standard.** Erase "without undue delay," within one month, across **all** systems including
backups. Regulators accept backups being put **"beyond use"** and overwritten on the normal
backup cycle rather than instant destruction; deletion logs must themselves avoid containing the
data subject's PII. The EDPB CEF 2025 action (results Feb 2026) holds up "extract from all
systems → restricted holding → permanent delete" as the exemplary pattern.
([Art. 17](https://gdpr-info.eu/art-17-gdpr/) ·
[ICO right to erasure](https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/individual-rights/individual-rights/right-to-erasure/) ·
[backups "beyond use"](https://verasafe.com/blog/do-i-need-to-erase-personal-data-from-backup-systems-under-the-gdpr/))

**Repo reality.** `DELETE /auth/me` (`routers/auth.py:204-283`) does a lot right: revokes the
Google **refresh** token (`:230`), purges R2 `source/` + `clips/` prefixes (`:258`), and
relies on FK `ON DELETE CASCADE` to clear the tenant tables (confirmed across `models.py` —
every tenant table FKs `creators.id` CASCADE). But:

- **Gap A (SEV-1):** immediately before deleting, it writes
  `append_audit(..., before={"channel_id": creator.channel_id, "email": creator.email})`
  (`routers/auth.py:268-275`). `audit_log` is **RLS-exempt** (`0010_rls_policies.py:35-36`),
  append-only, and **never purged** — so erasure *re-creates* the creator's email + channel_id
  in a store that survives forever. This is exactly the "deletion logs must not contain the data
  subject's PII" failure the regulators cite. **Fix:** log the deletion event **without** the
  PII payload (e.g. `before={"creator_id": str(creator_id)}` only — the id is already the
  entity_id), or hash/pseudonymize. Add a test asserting no email lands in `audit_log` on delete.

- **Gap B (SEV-1):** `event_logs.creator_id` is a **plain `sa.Uuid`, no ForeignKey, no CASCADE**
  (`models.py:724`), and the delete path never touches `event_logs`. So a deleted creator's
  telemetry rows (path, status, request_id, page, target, the `extra` JSONB) **persist
  indefinitely**, keyed to a now-orphaned `creator_id`. Telemetry is redacted of email/token at
  ingestion (`event_log.py:71-84`) — good — but `creator_id` + behavioural history is still
  personal data tied to an identifiable person while the creator exists, and orphaned data on a
  deleted user violates storage limitation + erasure. **Fix:** on account delete, `DELETE FROM
  event_logs WHERE creator_id = :id` on the logs engine (it is a separate DB/engine —
  `event_log.py:90`, so a single CASCADE won't reach it; needs an explicit cross-engine delete).
  Pairs with the retention schedule (gap D) which would cap it anyway.

- **Gap H (SEV-3):** R2 `delete_prefix` (`routers/auth.py:256-265`) removes live objects, but if
  the R2 bucket has **object versioning** or a lifecycle that keeps non-current versions, the
  bytes survive; and **DB backups** (Postgres PITR/snapshots) are undocumented. Neither is
  necessarily non-compliant — "beyond use + overwrite on the cycle" is acceptable — but it must
  be **documented** to be defensible. **Fix:** a short backup-erasure stance in
  `docs/COMPLIANCE.md` (backups are encrypted, access-restricted, and overwritten within N days;
  no restore re-introduces erased data without re-applying pending deletions).

**Note (cross-ref, not a new gap):** Google-side revocation is best-effort and the
`invalid_grant` row-delete path is solid (`youtube/oauth.py:251-260`); already documented in
`DECISIONS.md` 2026-05-28 (Issue 36).

### 3.2 Right of access + portability (GDPR Art. 15 / Art. 20; CCPA "right to know")

**Standard.** Art. 15 = give the data subject a copy of all their personal data + processing
info. Art. 20 = data they "provided," by automated means under consent/contract, in a
**structured, commonly-used, machine-readable** format — **CSV/XML/JSON**, not a PDF scan or
unlabeled dump. One month to respond. CCPA adds the "right to know" categories + specific pieces.
([Art. 20](https://gdpr-info.eu/art-20-gdpr/) ·
[ICO data portability](https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/individual-rights/individual-rights/right-to-data-portability/) ·
[Art. 15 access](https://gdpr-library.com/article/15))

**Repo reality.** **No export endpoint exists** — a grep of `routers/` for export/portability
finds only `/me/data-gate` (an onboarding readiness check, `routers/creators.py:194`), unrelated.
The Privacy Policy's "Your rights" section (`static/privacy.html:84-85`) offers **only deletion**.
This is the most clearly *missing* capability.

**Fix.** Build `GET /auth/me/export` (or `POST` → async job, mirroring the improvement-brief
202+poll precedent for the heavier joins): one authenticated creator, isolation-safe, emitting a
**JSON bundle** (machine-readable, self-describing) of their `creators` row, identity/DNA,
videos+metrics, feedback/outcomes, chat, billing ledger, and **presigned R2 links** (or a zip)
for their rendered clips. Must reuse `get_current_creator` so RLS + the app filter both apply —
the export touches exactly one tenant. Rate-limit it like `DELETE /auth/me` (`:205`, 5/hour).

### 3.3 Consent + transparency (GDPR Art. 6/7/13-14; CCPA notice-at-collection)

**Standard.** Lawful basis must be identified and disclosed; where consent is the basis it must
be freely given, specific, informed, **recorded**, and as easy to withdraw as to give. A
notice-at-collection must list categories, purposes, **sub-processors/recipients**, retention,
international transfer, and rights. CCPA requires a notice and (if applicable) a "Do Not Sell or
Share" link. ([CCPA/CPRA — CPPA FAQ](https://cppa.ca.gov/faq.html) ·
[OAG CCPA](https://oag.ca.gov/privacy/ccpa))

**Repo reality.**
- Consent is **implicit only**: `frontend/src/pages/Login.tsx:43` and `static/login.html:155`
  show "By signing in you agree to our [Terms]/[Privacy]" — no checkbox, **no recorded/timestamped
  consent artifact**. For contract-basis processing this is generally acceptable, but there is no
  evidence trail and no granular consent for any legitimate-interest telemetry.
- `static/privacy.html` (Last updated 2026-05-25, **"Draft — legal review pending"**) is accurate
  on YouTube Limited Use (Issue 78g) but **omits**: the named **sub-processors** (Anthropic,
  Voyage, Deepgram, Cloudflare, Stripe), the fact that **demographics describe the creator's
  audience** (third-party data), **international transfer** (all those vendors are US-based — see
  §4 transfer), **breach process**, and any **CCPA-specific** section. It also claims full
  deletion (`:85`) which gaps A/B contradict.
- **Honesty constraint** (CLAUDE.md): the policy must not over-claim. "We delete all your data"
  is currently an over-claim until A/B are fixed.

**Fix.** Rewrite the Privacy Policy to (1) name every sub-processor + purpose, (2) disclose the
audience-demographics processing and its aggregated nature, (3) state the transfer mechanism, (4)
add a CCPA section + "Do Not Sell or Share" statement (CreatorClip does **not** sell/share —
state it explicitly, which satisfies the requirement), (5) reference the export + deletion rights
accurately. Consider a recorded sign-up consent checkbox if counsel wants an evidence trail.

### 3.4 Retention + minimization (GDPR Art. 5(1)(c),(e))

**Standard.** Keep data no longer than necessary; define and enforce a retention period per data
class; storage limitation is enforceable independent of any erasure request.

**Repo reality.** Source media (72h) and YouTube analytics (30-day staleness) are enforced
(`worker/schedule.py`). **Unbounded today:** OAuth tokens of churned/inactive creators (no
"delete account after N days inactive" sweep), **`event_logs`** (explicitly "retention TBD …
candidate 90-day rolling purge", `docs/COMPLIANCE.md:87`), and **`audit_log`** (forever — may be
justifiable for security, but should be a *stated* period). The `invalid_grant` path deletes dead
token rows opportunistically (`youtube/oauth.py:256`) but only when a refresh is attempted.

**Fix.** Publish a retention schedule (table below) and add the missing Beat sweeps. CCPA also now
requires disclosing retention periods per category.

| Class | Proposed retention | Mechanism |
|---|---|---|
| `event_logs` | 90-day rolling purge | new daily Beat sweep |
| `audit_log` | e.g. 1–2 yr (counsel), then purge/anonymize | new sweep; today: forever |
| Tokens + account of inactive creator | e.g. delete after 12–24 mo inactivity (notice first) | new Beat sweep reusing the delete path |
| Source media / analytics | 72h / 30d (already enforced) | existing |

### 3.5 Telemetry as a PII risk (coordinate with prompts 05 + 11)

`event_logs` redacts email/token/secret-like keys at ingestion (`event_log._redact`,
`event_log.py:71-84`, with a broad substring blocklist `:39-56`) and stores creator as **id
only** — a genuinely good design. Residual risk: (1) it survives deletion (gap B); (2) `target`
and `extra` JSONB could capture a sensitive value whose *key* doesn't match the blocklist (e.g. a
free-text field) — worth a periodic audit of what keys actually appear. Cross-reference the
observability brief (05) for log-line PII and the notifications brief (11) for any email content
that becomes a new PII surface.

---

## 4. International transfer (cross-cutting)

**Standard.** EU/UK → US transfers need a valid mechanism: the **EU-US Data Privacy Framework**
(if the US vendor self-certifies) or **Standard Contractual Clauses + a Transfer Impact
Assessment**. ([EC EU-US transfers](https://commission.europa.eu/law/law-topic/data-protection/international-dimension-data-protection/eu-us-data-transfers_en) ·
[DPF program](https://www.dataprivacyframework.gov/Program-Overview))

**Repo reality.** Every sub-processor (Google, Anthropic, Voyage/MongoDB, Deepgram, Cloudflare,
Stripe) is **US-based**, and CreatorClip's hosting is US (Cloudflare Tunnel → `autoclip.studio`).
If any EU/UK creator is onboarded, transfer is in scope and currently **undocumented**. This is a
**counsel + target-jurisdiction question** (§5) more than a code change, but the Privacy Policy
must state the mechanism.

---

## 5. Sub-processors & DPAs (Art. 28 / Art. 30)

**Standard.** A controller must have an Art. 28 **DPA** with each processor that flows down the
same obligations, plus a maintained **Record of Processing** (Art. 30) and ideally a public
sub-processor list. ([Art. 28](https://gdpr-info.eu/art-28-gdpr/) ·
[ICO contract requirements](https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/accountability-and-governance/contracts-and-liabilities-between-controllers-and-processors-multi/what-needs-to-be-included-in-the-contract/))

**Vendor posture (researched — confirms DPAs/no-train are *available*, must be *activated*):**

- **Anthropic:** API inputs/outputs **not used for training** by default; DPA is auto-incorporated
  into the Commercial Terms (no separate signature); default API log retention dropped to **7 days**
  (2025-09-14), 30-day opt-in via DPA; ZDR available on approval.
  ([Anthropic DPA / retention](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention) ·
  [ZDR](https://privacy.claude.com/en/articles/8956058-i-have-a-zero-data-retention-agreement-with-anthropic-what-products-does-it-apply-to))
  → **Action:** confirm Commercial Terms (not consumer) are in force; consider ZDR for transcripts.
- **Voyage (MongoDB):** opt-out of storage/training → **zero-day retention** (org admin + payment
  method). ([Voyage privacy](https://www.voyageai.com/privacy)) → **Action:** enable opt-out.
- **Deepgram** (only if `TRANSCRIPTION_BACKEND=deepgram`): trains **only** via the opt-in Model
  Improvement Program; pass `mip_opt_out=true` and/or a DPA to disable + minimize retention.
  ([Deepgram data security](https://deepgram.com/data-security)) → **Action:** if hosted backend
  is ever enabled, set `mip_opt_out=true` and sign the DPA. (Default backend is WhisperX, local —
  lowest-risk; note this in the policy.)
- **Cloudflare R2, Stripe, Google:** all offer standard DPAs + are DPF-relevant → **Action:**
  ensure each DPA is accepted/on file.

**Repo reality.** No DPA/Art. 30 artifacts exist in `docs/`. The vendors are named in `docs/SOT.md`
but no privacy-facing sub-processor list exists. **Fix:** create `docs/SUBPROCESSORS.md` (the
Art. 30 record + the public-facing list) and an ops checklist confirming each DPA is executed and
each no-train/min-retention switch is enabled.

---

## 6. Proposed issues (dependency-ordered, `docs/issues.md` house style)

> Each notes the `docs/COMPLIANCE.md` / `docs/DECISIONS.md` entry it requires. Numbers are
> placeholders pending the queue; sequence is the dependency order.

### Issue 177a — [SEV1] Erasure leak: stop writing deleted-creator PII to `audit_log`
**What:** `DELETE /auth/me` (`routers/auth.py:268-275`) persists the deleted creator's `email` +
`channel_id` into the never-purged, RLS-exempt `audit_log`. Remove the PII from the deletion
audit payload (keep the `creator_id`/entity_id only, or pseudonymize).
**Acceptance criteria:**
- [ ] Deletion audit row contains no `email` and no `channel_id` (and no other PII)
- [ ] Integration test: after `DELETE /auth/me`, `SELECT … FROM audit_log` shows no email for that creator
- [ ] `docs/COMPLIANCE.md` Privacy Posture updated to state the deletion-log minimization rule
- [ ] DECISIONS entry: deletion logs must not re-introduce erased PII (cite EDPB CEF 2025)

### Issue 177b — [SEV1] Erasure completeness: purge `event_logs` on account deletion
**What:** `event_logs.creator_id` has no FK/CASCADE (`models.py:724`) and lives on a separate
engine (`event_log.py:90`); a deleted creator's telemetry persists forever. Add an explicit
cross-engine `DELETE FROM event_logs WHERE creator_id = :id` to the deletion path.
**Acceptance criteria:**
- [ ] Account deletion removes all `event_logs` rows for that creator (separate logs engine handled)
- [ ] Integration test seeds telemetry for two creators, deletes one, asserts only theirs is gone
- [ ] Best-effort failure does not abort deletion (mirror the R2-purge try/except posture)
- [ ] `docs/COMPLIANCE.md` data-class table: `event_logs` row notes deletion + retention

### Issue 177c — [SEV1] Data export endpoint (Art. 15/20 access + portability)
**What:** No export exists. Add an isolation-safe, machine-readable (JSON) export of one
creator's data + presigned clip links, async (202+poll) like the improvement brief.
**Acceptance criteria:**
- [ ] `GET`/`POST /auth/me/export` authed via `get_current_creator`; touches exactly one tenant
- [ ] Output is structured JSON (CSV/JSON acceptable) covering profile, DNA/identity, videos+metrics, feedback/outcomes, chat, billing; clips via presigned links or zip
- [ ] Rate-limited (match `DELETE /auth/me`); RLS + app filter both enforced (isolation test)
- [ ] Privacy Policy "Your rights" updated to describe access/portability accurately
- [ ] DECISIONS entry: export format + scope ("provided by" vs derived data) choice

### Issue 177d — [SEV2] Retention schedule + missing purge sweeps
**What:** Define + enforce retention for `event_logs` (90d), `audit_log` (counsel-set), and
inactive-creator tokens/accounts; document the schedule.
**Acceptance criteria:**
- [ ] Daily Beat: `purge_stale_event_logs` (configurable days, default 90)
- [ ] Inactive-account policy decided + (if adopted) a notice-then-delete sweep
- [ ] Retention table added to `docs/COMPLIANCE.md` (per data class, incl. CCPA disclosure)
- [ ] DECISIONS entry: chosen retention periods + rationale

### Issue 177e — [SEV2] Sub-processor DPAs, Art. 30 record, public list
**What:** Execute/confirm DPAs with all vendors, enable no-train/min-retention switches, publish
`docs/SUBPROCESSORS.md` (Art. 30 record + public list).
**Acceptance criteria:**
- [ ] Each vendor DPA confirmed on file (Anthropic Commercial Terms, Voyage opt-out, Deepgram MIP opt-out if used, R2, Stripe, Google)
- [ ] `docs/SUBPROCESSORS.md` lists name, purpose, data categories, region, transfer mechanism
- [ ] Voyage zero-retention opt-out enabled; Deepgram `mip_opt_out=true` if hosted backend used
- [ ] `docs/COMPLIANCE.md` references the sub-processor record

### Issue 177f — [SEV2] Privacy Policy + consent accuracy rewrite
**What:** Make `static/privacy.html` (+ SPA equivalent) accurate: sub-processors, audience-demographics
disclosure, international transfer, CCPA section + "do not sell/share" statement, accurate
rights (export + corrected deletion claim post-177a/b). Decide on a recorded sign-up consent.
**Acceptance criteria:**
- [ ] Policy names sub-processors + transfer mechanism + breach contact
- [ ] CCPA notice-at-collection + "we do not sell or share" present
- [ ] Audience-demographics (aggregated, audience = third parties) disclosed
- [ ] Deletion/export claims match implemented behaviour (no over-claim — honesty constraint)
- [ ] `tests/test_static.py` pins the new required clauses (mirror the Limited-Use test, Issue 78g)
- [ ] DECISIONS entry if a recorded-consent checkbox is added

### Issue 177g — [SEV2] Breach-notification runbook (Art. 33/34)
**What:** Add a runbook to `docs/RUNBOOKS.md`: detection → 72h supervisory-authority notify →
processor-notify chain → Art. 34 high-risk subject notice; templates + contacts.
**Acceptance criteria:**
- [ ] Runbook covers the 72h clock, content required (Art. 33(3)), and the high-risk subject-notice threshold
- [ ] Processor breach-notify expectations referenced from each DPA (177e)
- [ ] Owner + escalation path named

### Issue 177h — [SEV3] Backup / R2-versioning erasure stance
**What:** Document (and verify) that R2 versioning/lifecycle + DB backups are "put beyond use"
and overwritten on a defined cycle so erasure is defensible.
**Acceptance criteria:**
- [ ] `docs/COMPLIANCE.md` states the backup-erasure stance ("beyond use" + overwrite window)
- [ ] R2 bucket versioning/lifecycle for `source/` + `clips/` documented; no restore re-introduces erased data
- [ ] DECISIONS entry citing the regulator "beyond use" position

---

## 7. Open questions for the human / counsel (one-line answers)

1. **Target jurisdictions at launch** — US-only, or EU/UK creators too? (Decides whether full GDPR + international transfer is in scope now or later.)
2. **Controller vs processor** — CreatorClip is the **controller** for creator account data; confirm with counsel (affects who owns the Art. 30 record vs the vendor DPAs).
3. **Lawful basis sign-off** — confirm "performance of contract" for core features and "legitimate interest" for `event_logs` telemetry (the §2 proposals).
4. **Is a DPA on file with each vendor?** Anthropic (Commercial Terms, not consumer?), Voyage, Deepgram (only if hosted), Cloudflare, Stripe, Google — which are signed today?
5. **`audit_log` retention** — what period does security/ops need before purge/anonymize?
6. **Inactive-account policy** — adopt an auto-delete-after-N-months-inactive sweep, or retain until explicit deletion?
7. **Backups** — what is the actual DB backup/PITR retention and R2 object-versioning config (needed to write 177h honestly)?
8. **Sale/share** — confirm CreatorClip never sells or shares personal info for cross-context advertising (assumed true from the codebase; counsel to confirm so the CCPA statement is accurate).

---

## 8. Stale / contradictory docs flagged (not papered over)

- `static/privacy.html:85` + `static/tos.html` §3 claim full data deletion — **contradicted** by
  gaps A (audit_log) and B (event_logs) until 177a/b ship.
- `static/privacy.html:51` and `static/tos.html` are marked **"Draft — legal review pending"**
  (Last updated 2026-05-25) — consistent with this brief's conclusion that the policy is not
  launch-ready.
- `docs/COMPLIANCE.md:87` `event_logs` retention is "TBD" — resolved by 177d.
- `docs/COMPLIANCE.md` Pre-Public-Launch gates list account-deletion as `[ ]` (unchecked) at
  `:147` even though Issue 158 shipped the UI and the endpoint exists — **bookkeeping rot**;
  `docs/PROJECT_STATE.md` and CLAUDE.md treat it as ✅. Reconcile when 177a/b land.

---

### Sources

- GDPR Art. 17 — https://gdpr-info.eu/art-17-gdpr/ · ICO erasure — https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/individual-rights/individual-rights/right-to-erasure/ · backups "beyond use" — https://verasafe.com/blog/do-i-need-to-erase-personal-data-from-backup-systems-under-the-gdpr/
- GDPR Art. 20 — https://gdpr-info.eu/art-20-gdpr/ · ICO portability — https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/individual-rights/individual-rights/right-to-data-portability/ · Art. 15 — https://gdpr-library.com/article/15
- GDPR Art. 28 — https://gdpr-info.eu/art-28-gdpr/ · ICO Art. 28 contract — https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/accountability-and-governance/contracts-and-liabilities-between-controllers-and-processors-multi/what-needs-to-be-included-in-the-contract/
- GDPR Art. 33 — https://gdpr-info.eu/art-33-gdpr/ · EDPB breach guidelines 9/2022 — https://www.edpb.europa.eu/system/files/2023-04/edpb_guidelines_202209_personal_data_breach_notification_v2.0_en.pdf
- CCPA — https://oag.ca.gov/privacy/ccpa · CPPA FAQ — https://cppa.ca.gov/faq.html
- EU-US transfers — https://commission.europa.eu/law/law-topic/data-protection/international-dimension-data-protection/eu-us-data-transfers_en · DPF — https://www.dataprivacyframework.gov/Program-Overview
- Anthropic API retention/DPA — https://platform.claude.com/docs/en/manage-claude/api-and-data-retention · ZDR — https://privacy.claude.com/en/articles/8956058-i-have-a-zero-data-retention-agreement-with-anthropic-what-products-does-it-apply-to
- Voyage privacy — https://www.voyageai.com/privacy · Deepgram data security — https://deepgram.com/data-security
