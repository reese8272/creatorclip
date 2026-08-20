# AUDIT_KNOWN_ISSUES — what we already suspect

> **Part B. Please read `docs/AUDIT_BRIEF.md` and do a cold pass before this file.**
> Everything below is already on our radar. The point of handing it over *after* your first
> pass is that the delta — what you found that isn't here — is the actual value of the
> review. Once you've read this, it should stop you writing up things already filed.

**Written 2026-08-15**, against `claude/code-audit-prep-eg5128`. Every claim here was
verified on this tree, and each says how confident we are and why. Where something is *not*
exploitable, it says so — we'd rather hand you a calibrated list than an alarming one.

---

## A. Found while writing this brief — verified, unfiled until now

### A1. The Layer-0 security gate has never scanned 8,277 lines of source

**Confidence: certain. Reproduced below. Filed as Issue 497.**

`run_layer0.py:226` builds bandit's target list as:

```python
dirs = [s for s in _sources() if not s.endswith(".py")]
proc = _run(["bandit", "-r", *dirs, "-f", "json", "-q"])
```

`-r` takes directories, so **every root-level `.py` file in the explicit source list is
silently dropped**. Separately, three packages were never added to `_CANDIDATE_SOURCES`
(`run_layer0.py:44-63`) as the codebase grew, so **mypy never type-checks them either**.

Reproduce in 20 seconds:

```bash
.venv/bin/python -c "
import sys; sys.path.insert(0,'.claude/skills/production-assessment/scripts')
import run_layer0 as r
srcs = r._sources()
print('bandit scans:', [s for s in srcs if not s.endswith('.py')])
print('bandit DROPS:', [s for s in srcs if s.endswith('.py')])
"
```

```
bandit scans: ['routers','youtube','ingestion','dna','clip_engine','preference',
               'knowledge','upload_intel','improvement','worker','billing']
bandit DROPS: ['auth.py','config.py','crypto.py','db.py','limiter.py','main.py','models.py']
```

| Scope | Never checked | Lines |
|---|---|---|
| **mypy** | `analysis/`, `chat/`, `notify/`, `api_key.py`, `event_log.py`, `flags.py`, `observability.py`, `redact.py`, `shared_resources.py`, `verbose.py` | **4,093** |
| **bandit** | all of the above **plus** `auth.py`, `config.py`, `crypto.py`, `db.py`, `limiter.py`, `main.py`, `models.py` | **8,277** |

Why this matters more than the raw number: the unscanned set is disproportionately the
security-load-bearing code — `crypto.py` (Fernet token encryption), `auth.py` (JWT +
session), `api_key.py` (key generation and hashing), `redact.py` (**the PII scrubber
itself**), `flags.py` (the kill switch gating billed LLM calls), and `config.py` (secret
validation). Meanwhile `chat/` — one of the never-type-checked packages — is where two of
the July SEV2s landed (an unbilled nested LLM call, a missing kill-switch gate).

**Honest severity: this is a hole in the gate, not a live vulnerability.** We ran both tools
against every excluded path:

```
bandit  →  1 LOW finding (B110, try/except/pass).  0 HIGH, 0 MEDIUM.
mypy    →  0 errors.
```

So nothing is hiding there *today*. The defect is that for the project's entire life, the
gate could not have told anyone if something were — while reporting `bandit high 0 /
medium 0` and `All runnable gates passed`. That makes it instance **#4** of the failure
mode in §6 of the brief, and the reason we're asking you to hunt for #5.

**Deliberately not fixed before your review**, so your pass isn't contaminated by a
concurrent change to the thing you're being asked to look at.

---

## B. Carried open findings from the last internal assessment

From `docs/assessment/REPORT.md`, dated **2026-07-29 — now ~2.5 weeks stale**, and it
predates the catalog-sync incident. Treat its "all clean" verdicts as expired.

| Sev | Where | What |
|---|---|---|
| SEV2 | `worker/tasks.py:882` | `build_signals` enqueues `generate_clips` unconditionally on task **redelivery** → double LLM spend. The `try/except` around it guards broker failure only, and says so. Needs a dedup (existing-clips probe before enqueue). *(The 2026-07-29 report cites `:511`; that line has drifted — the enqueue is now at 882.)* |
| SEV2 | `routers/clips.py` ×3 | In-request LLM call on `/clips/generate`; `create_summary` budget-parity gap; per-feedback retrain enqueue. Designs sketched in `docs/assessment/modules/routers.md`. |
| SEV2 | `clip_engine/reframe.py` | ffmpeg `sendcmd` path unverified against a real ffmpeg build. **The gating condition on this finding has since changed** — the flag was turned ON in prod (Issue 422), so the "it's feature-gated off" mitigation no longer holds. Worth a look. |

Also carried, from `docs/OFF_COURSE_BUGS.md` (~51 open rows; these are the ones we'd
most like a second opinion on):

- **SEV2** — production migrations run with **no pre-migration safety dump**.
  `BACKUP_R2_BUCKET` has never been set, so every prod migration to date ran unprotected.
  Latent only until a destructive migration exists.
- **BLOCKER (partially addressed)** — the 500 MB upload cap rejected typical creator footage
  (1–3 GB OBS recordings). Code shipped as Issue 395, but three acceptance drills
  (>2 GB, reload-resume, JWT-expiry-mid-upload) were never run.
- **SEV2** — Playwright and visual-regression CI jobs fail on the self-hosted runner
  (missing `libatk`, no passwordless sudo) and fail-fast, so **0 tests run**.

---

## C. Structural — known, unresolved, and we want your opinion

1. **`worker/tasks.py` is 7,179 lines.** 41 task functions plus helpers. Question 4 in the
   brief asks you to make an actual call on this rather than just flag the number.
2. **`routers/clips.py` is 2,893 lines** across 27 endpoints — the largest HTTP surface and
   the one carrying three of the four open SEV2s above.
3. **Coverage floors are enforced on only 5 modules** — `clip_engine` 91, `preference` 88,
   `crypto` 99, `limiter` 99, `auth` 99 — plus a global 83%. There is **no per-module floor
   at all** on `routers/`, `worker/`, `billing/`, `youtube/`, `chat/`, `knowledge/`, `dna/`,
   `ingestion/`. A regression concentrated in `worker/` is invisible until it moves the
   global rate by ~0.5 points.
4. **70 skipped tests.** 53 are dead-page residue from Issue 226 and should be *deleted*,
   not skipped. But two are worse: `tests/test_notifications.py:636` and
   `tests/test_notifications_triggers.py:652` are marked `@pytest.mark.skip` with reason
   "needs real Postgres" — when the project **has** an integration lane with live PG+Redis
   running on every PR. They should carry `@pytest.mark.integration` and actually run.
5. **`mutmut` (the only gate measuring whether tests *assert* rather than merely execute)
   targets 3 files**: `clip_engine/scoring.py`, `preference/decay.py`, `crypto.py`. It's
   weekly and never gating.
6. **`detect-flakes` in CI is `continue-on-error: true`** — it surfaces flake candidates
   into the job summary but can never fail a build. There are three documented, unresolved
   flakes (two vitest, one `test_ranking_persist_race`) whose failing seed was never
   captured.

---

## D. Money path

**`routers/billing.py:262-314` — the Stripe webhook grants `pack.minutes` without ever
comparing `cs["amount_total"]` to `pack.price_cents`.** It reads `pack_id` from session
metadata, looks up the catalog pack, and grants its full minutes.

**Be precise about severity: this is not exploitable today.** `billing/stripe_client.py`
does not set `allow_promotion_codes`, which Stripe defaults to `false`, so there is no
discount path that could produce an `amount_total` below the catalog price. It is:

- a **latent regression trap** — the day someone enables promo codes or creates a Session
  from the Stripe dashboard, it silently grants full minutes at a discount; and
- a **ledger-accuracy gap** — `grant_minutes` records `price_cents=pack.price_cents`, the
  catalog price rather than the amount actually collected, so reconciliation would not
  catch the divergence.

Also on this path:

- **`checkout.session.async_payment_succeeded` is referenced in a comment
  (`routers/billing.py:256`) but not handled.** ACH / bank transfer / BNPL purchases
  complete the Checkout flow and then never fulfill.
- **Idempotency is per-`checkout.session.id`, not per-`event.id`** — fine for the one event
  currently handled, but it won't generalise when a second event type is added.
- **`record_llm_usage` swallows all exceptions** (`billing/ledger.py:253`) and writes via the
  BYPASSRLS admin session, so LLM cost accounting can silently drop rows under load.

What's genuinely good here, so you don't re-derive it: webhook signature verification is
correct and rate-limited *in front of* the signature check; the `payment_status == "paid"`
guard is right per Stripe's fulfillment docs; the checkout idempotency key is derived
server-side with a creator prefix; and ledger races are closed **structurally** with UNIQUE
constraints + `IntegrityError` catches rather than read-then-write guards.

---

## E. Deployment and configuration

1. **`Dockerfile` has no `USER` directive — containers run as root**, and the default `CMD`
   carries `--reload`. Every production service overrides `command:`
   (`docker-compose.prod.yml`, `render.yaml`), so the dev server does **not** actually ship
   today. It's a footgun, not a live defect — one missed override and a reload-mode server
   goes to prod.
2. **`render.yaml:41-44` commits `VERBOSE_LOGGING=true` *and*
   `VERBOSE_LOGGING_ALLOW_PROD=true`.** With `LOG_DIR=""` this streams raw prompts, raw LLM
   responses, full transcripts, full request bodies and full ffmpeg commands — including PII
   — to the log aggregator through a deliberately **non-scrubbing** formatter, bypassing
   `redact.py` entirely.

   Context that changes how you should read it: this is a *documented* beta deviation
   (`docs/DECISIONS.md`, 2026-06-29), the two-flag design exists precisely so a routine
   deploy can't enable it by accident, `.env.example` defaults both to `false`, and the file
   itself carries a "set BOTH to false before public launch" note.

   **But `render.yaml` is not the live path** — production is the DigitalOcean VM running
   `docker-compose.prod.yml` against an untracked `/opt/autoclip/.env`. **The actual
   production value cannot be determined from this repo.** This is an operator check for the
   owner, not a finding against the code. Flagging it because it's the highest-consequence
   unverifiable item in the tree.
3. **RLS may be enabled but bypassed.** `.github/workflows/activate-rls.yml` is a *manual*
   `workflow_dispatch` that switches `DATABASE_URL` to the non-BYPASSRLS `creatorclip_app`
   role. `render.yaml:134` points `DATABASE_MIGRATION_URL` at the **same DSN** as
   `DATABASE_URL`. If prod connects as the privileged role, tenant isolation rests entirely
   on the application-layer predicates in `routers/_owned.py`. Worth verifying which role is
   actually in use.
4. **`event_logs` has no RLS policy at all** — isolation is application-only, and erasure
   must delete it manually since there's no FK cascade.
5. **`.env.example` drift:** `CELERY_SOFT_TIME_LIMIT_S` and `YOUTUBE_PUBLISH_PRIVACY` are
   read by `config.py` but undocumented in `.env.example`. Both have defaults so nothing
   breaks — but the first is the single source of truth for the
   soft < hard < visibility-timeout invariant, and the second controls **whether uploads land
   public**. Undocumented is a real operator trap.

---

## F. Erasure / GDPR

`routers/auth.py:429-581` — **erasure is best-effort on everything except the DB cascade.**
OAuth token revocation, R2 media purge, and `event_logs` purge each swallow exceptions, so a
right-to-erasure request can report success while media and telemetry survive. The existence
of `scripts/reapply_erasures.py` implies this has bitten before.

Related: renders live at non-creator-scoped R2 keys (`clips/{clip_id}.mp4`), so erasure
depends on `worker/erasure.py:candidate_keys_for_creator` enumerating completely. That key
namespace is the weak point.

Also worth checking: **`generate_data_export`** (`worker/tasks.py:5133`) walks every table
via `_row_to_dict` into a creator-downloadable JSON bundle. Confirm no `*_encrypted` column
or `key_hash` is serialized into it.

---

## G. LLM surface

Mostly in good shape — flagging what's left so you don't spend the time re-deriving it.

**Already solid:** clients are module-level singletons (a per-request-construction SEV1 was
fixed); prompt caching is applied deliberately with a `_CACHE_FLOOR_TOKENS = 1024` guard and
correct cache read/write price multipliers; a `UNTRUSTED_CONTENT_POLICY` block is injected
into every system prompt and **pinned by structural tests**; attacker-influenceable text is
JSON-wrapped into a labelled envelope in the *user* turn per OWASP LLM01; and unknown model
families fall back to **Opus rates** so a misconfiguration can never under-bill. Every
LLM-reaching endpoint carries both `require_flag("llm_generation")` and `require_budget` —
that gating is complete and CI-enforced.

> **Corrected 2026-08-20 (Issue 506).** "CI-enforced" was true of the *routes someone had
> remembered to list*, not of the route table: the enforcing test named 10 of 17 live LLM routes and
> could not detect a new one. It is now derived from the live route table. Fixing it surfaced four
> LLM routes with a daily cap but no burst limit (or the reverse), and one render route —
> `POST /videos/{video_id}/summaries` — carrying the `render_intake` kill switch with **no
> `require_budget` at all**. All five are fixed; the claim above is now accurate.

**Where we'd still look:**

- **`improvement/brief.py`** is the highest-risk path — it uses server-side `web_search`
  (`max_uses: 5`), so third-party SEO-influenced content enters the context. The policy
  block is the only mitigation. Confirm its output can't drive a privileged action.
- **`chat/tools.py`** — 8 tools, each correctly taking `creator_id` as a server-supplied
  positional argument rather than from tool input. Worth confirming `execute_tool` can't be
  coerced into a different `creator_id`, and that `_suggest_clip_titles` (a tool that itself
  calls an LLM) can't be looped for cost amplification within one turn.
- **`routers/insights.py:911`** bypasses the shared streaming helpers with a raw
  `messages.create` and its own token accounting — the one non-uniform call site.

---

## H. Known doc drift

Report more if you find it — this is a real hazard here.

- `docs/issues.md` header still says "Active lane: L26"; L27, L28 and L29 are all filed and
  complete.
- `docs/DECISIONS.md:12902` describes the Google OAuth app as "In production / External /
  1-of-100"; `docs/GO_LIVE.md` and `docs/ACCESS.md` say "Testing". **`GO_LIVE.md` wins** —
  verification (Issue 29) is unsubmitted, which is why refresh tokens expire every 7 days.
  (`GO_LIVE.md` flags this drift itself but cites `:12687`, which has since moved.)
- **`CLAUDE.md` — the project's own governing rules file — cites `WINDOW_S = 75.0` as living
  in `clip_engine/window.py`.** It's actually `clip_engine/candidates.py:22`; `window.py`
  exists but is the signal-array builder. Also, `CLAUDE.md` → *Project Structure* omits
  `analysis/`, `chat/`, `notify/` and `billing/` — the same stale list that seeded the
  Issue 497 gate hole.
- `docs/assessment/REPORT.md` is dated 2026-07-29 and predates the catalog-sync incident; its
  module "clean" verdicts are stale.
- `LEFT_OFF.md` says next free issue number is 496; `docs/issues.md` says 497. The latter is
  correct.

---

## I. Explicitly out of scope

Filed, understood, deliberately not being worked — no need to report these:

- The GKE / Helm track (`deploy/charts/`) — descoped for v1, never deployed, placeholder
  values throughout.
- Lane L25 Batches C/D/E (B-roll, multi-track timeline, transitions) — filed, unfunded.
- Issue 445 (three-pile triage UI) — known unbuilt, four design questions still open.
- Issue 484 (a clip that opens mid-clause and inverts the speaker's meaning) — known,
  filed, the highest-impact clip-quality defect.
- Issue 495 — an 8-item deferred triage list from the 2026-08-14 pass.
