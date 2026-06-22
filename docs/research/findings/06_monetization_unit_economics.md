# Findings — Monetization, Pricing & Unit Economics (Issue 171)

> Read-only research brief. No product code changed. Repo claims cite `file_path:line`;
> external claims cite source links. Cost feeds (LLM token mechanics) are cross-referenced
> from prompt `docs/research/02_agentic_caching_cost.md` rather than re-derived — see the note
> in §2.
>
> **Date:** 2026-06-22 · **Author:** monetization research agent · **Tracked as:** Issue 171.

---

## 1. Executive summary

**The minute-pack model is the right primitive and is safely profitable on variable cost — but
three things are unbuilt or contradictory and must be resolved before public launch.**

1. **Unit economics are healthy at the variable level, dominated by render compute, not LLM.**
   At the *cheapest* pack price (Studio, **4.5¢/min**, `billing/packs.py:34`) a typical 60-min
   upload costs roughly **¢25–60 of direct third-party + compute cost** against **$2.70 of
   revenue** — a gross margin north of **80%** even on the worst pack. The binding constraint is
   **not** any single video; it is (a) **render compute fan-out** (N clips × ffmpeg CPU-seconds,
   the one cost that scales with *outputs* not *input minutes*) and (b) the **absence of a
   per-creator pre-job quota** — a single creator scripting the API can run many renders/LLM
   calls per deducted minute and invert the economics. **The margin guard that matters is a
   rate/quota gate, not the per-minute price.** (See §4.)

2. **There is no usage cap or rate limit *before* an LLM/render job** beyond the balance check
   and the chat daily quota. `routers/clips.py` gates render on `check_positive_balance`
   (`routers/clips.py:206,385,575,685`) — a *floor*, not a *ceiling*. A creator with 1 minute of
   balance can trigger an unbounded number of re-renders / re-scorings of already-paid videos.
   This is the explicit CLAUDE.md pre-launch item ("Per-creator rate limiting + usage quotas
   before each LLM/render job"). **Highest-severity gap.**

3. **Billing-correctness is strong on the purchase path but has real revenue-leak/UX gaps:**
   webhook idempotency is correctly belt-and-suspenders (`routers/billing.py:217-239`,
   `billing/ledger.py:61-98`), but there is **no Stripe Tax** (`billing/stripe_client.py:70-93`
   has no `automatic_tax`), **no dunning/failed-payment handling** (irrelevant for one-time
   payments today, but a blocker the moment any recurring tier ships), **no reconciliation job**
   between Stripe and the `minute_packs` ledger, and **refunds are minutes-only** — a creator who
   pays and is dissatisfied has no money-back path, only an automatic *minutes* refund on
   *terminal ingest failure* (`billing/refund.py:36`). (See §3.)

4. **The docs contradict themselves on packaging.** `docs/COMPETITIVE_RESEARCH.md:113` recommends
   *"avoid per-input-minute credits… use per-output-clip or flat-subscription tiers"*, while the
   shipped product and `docs/DECISIONS.md` (Issue 125, 2026-06-08) commit to **per-input-minute
   packs** and Issue 152 (2026-06-17) explicitly rejects subscriptions. This is a live, unresolved
   strategic tension — flagged, not papered over (see §5 Open Question 1). **Recommendation: keep
   per-input-minute as the billing unit (it is the 2026 category standard and matches the ledger),
   but neutralize its one real weakness — punishing 3–8h streams — with a long-form/stream
   discount tier or a per-minute taper, and lead the funnel with a free-trial that reaches a first
   clip.**

**Recommended packaging (v1, honest naming):** keep the 5 one-time minute packs; **add a
documented per-minute taper rationale**, **add an explicit "Stream" pack** sized for long VODs,
keep the **7-day / 60-min free trial** but **verify the grant covers a real first clip** (§6),
and defer any subscription tier until ≥30 days of real per-video cost telemetry justify it
(consistent with the Issue 152 decision to meter AI against the existing credit currency).

---

## 2. Cost-per-video model

### 2.1 Cost inputs (current, official sources — June 2026)

| Input | Unit cost | Source |
|---|---|---|
| Transcription — Deepgram Nova-3 (default `TRANSCRIPTION_BACKEND=deepgram`, `config.py:84`) | **$0.0043/min** mono prerecorded ($0.0052 multichannel) | [Deepgram pricing](https://deepgram.com/pricing); [BrassTranscripts breakdown](https://brasstranscripts.com/blog/deepgram-pricing-per-minute-2025-real-time-vs-batch) |
| LLM — Claude Sonnet 4.6 (`ANTHROPIC_MODEL`, `config.py:65`) | **$3 / $15** per MTok (in/out); cache read **0.1×** | [Anthropic API pricing 2026 (Finout)](https://www.finout.io/blog/anthropic-api-pricing); [pecollective rates](https://pecollective.com/tools/anthropic-api-pricing/) |
| LLM — Claude Haiku 4.5 (titles/hooks/chapters/insights) | **$1 / $5** per MTok | same sources (one outlier headline cites Haiku at $0.25 in — treated as stale/SKU-specific; used $1/$5 as the consistent 2026 figure) |
| Embeddings — Voyage 3.5 (`dna/embeddings.py:84`) | **$0.06** per MTok; first 200M free | [Voyage AI pricing](https://docs.voyageai.com/docs/pricing) |
| Object storage — Cloudflare R2 | **$0.015/GB-mo**; **egress FREE**; Class A $4.50/M, Class B $0.36/M ops | [Cloudflare R2 pricing](https://developers.cloudflare.com/r2/pricing/) |
| Render compute — ffmpeg cut + 9:16 reframe + caption burn (`clip_engine/render.py`) | self-hosted CPU; **est. $0.002–0.01 / output clip** on a shared worker (no GPU; OpenCV Haar + libass, single-pass) | derived (no GPU dependency in render path); cross-check K8s node cost at deploy |

> **Cross-reference (prompt 02).** The per-call *token mechanics* (cache-hit rates, the batched
> single scoring call, the inert-cache-marker history) are owned by
> `docs/research/02_agentic_caching_cost.md`. That findings file **does not yet exist on disk**
> (`docs/research/findings/02_*` is missing as of this writing) — flagged. Token *dollar* figures
> below use the published Anthropic rates above and the repo's observed call shapes
> (`max_tokens` and cache breakpoints read directly from source), so this model stands alone, but
> it should be reconciled against 02's per-operation token table when that lands.

### 2.2 What actually runs per video (call fan-out)

Critically, **the expensive LLM step does not fan out per clip.** Clip scoring is a **single
batched Sonnet call** — all candidates go in one payload with the DNA brief cached
(`clip_engine/scoring.py:209-250`); DNA synthesis is **per-channel, amortized**, not per-video
(`dna/brief.py:139`, model = `ANTHROPIC_MODEL`, `max_tokens=2000`). Title/hook/chapter/thumbnail
generation is **on-demand and optional** (Haiku for hooks/chapters/insights at $1/$5;
`knowledge/hooks.py:212`, `knowledge/chapters.py:207`, `routers/insights.py:579`). So per
*processed* video, the mandatory LLM cost is essentially **one scoring call**.

### 2.3 Cost-per-video table (illustrative, mandatory pipeline only)

Assumes Deepgram transcription, one batched Sonnet scoring call with DNA cached (cache read at
0.1×), R2 storage of source audio + rendered clips for ~30 days, and render of a representative
clip count. Prices rounded; **render compute is the dominant uncertainty** and is shown as a
range.

| Video | Minutes deducted | Transcription | Scoring (Sonnet, cached DNA)¹ | Storage/ops (R2, ~30d)² | Render (N clips)³ | **Total direct cost** | Revenue @ Studio 4.5¢/min | Revenue @ Starter 9¢/min | **Margin @ Studio** |
|---|---|---|---|---|---|---|---|---|---|
| 10-min upload | 10 | $0.04 | $0.01–0.03 | <$0.01 | 5 clips → $0.01–0.05 | **~$0.07–0.13** | $0.45 | $0.90 | **~71–84%** |
| 60-min upload | 60 | $0.26 | $0.02–0.06 | ~$0.02 | 10 clips → $0.02–0.10 | **~$0.32–0.44** | $2.70 | $5.40 | **~84–88%** |
| 3-hr stream | 180 | $0.77 | $0.05–0.15⁴ | ~$0.05 | 20 clips → $0.04–0.20 | **~$0.91–1.17** | $8.10 | $16.20 | **~86–89%** |

¹ One Sonnet call, `max_tokens=1200` (`clip_engine/scoring.py:239`); DNA brief cached at 0.1×
read after first build. Output ≈ 1.2k tok × $15/MTok ≈ $0.018; input dominated by transcript
context, mostly cached on repeat scorings.
² R2 storage is pennies and **egress is free** — clips delivered to creators incur **$0 transfer
cost**, a real structural advantage over S3. Class A/B ops are negligible at this volume.
³ Render is self-hosted ffmpeg (no GPU in the path, `clip_engine/render.py:34-36`). Cost = worker
CPU-seconds × node hourly rate; the **only cost that scales with output count**, not input
minutes. Pin this with a real K8s node rate at deploy.
⁴ Long streams produce more candidates → larger uncached transcript payload per scoring call;
still bounded by `max_tokens=1200` output and the cached DNA prefix.

**Where margin goes negative:** never on a single honestly-deducted video at any pack price. It
goes negative only when **outputs decouple from deducted minutes** — i.e. a creator re-renders /
re-scores the same already-paid video many times, or scripts the API to fan out renders. That is a
**quota problem, not a pricing problem** (§4). The operation that *dominates variable cost* is
**render compute**, followed by **transcription**; LLM scoring is the *smallest* line.

---

## 3. Billing-correctness findings

Each finding: the standard (with link) → repo reality (`file_path:line`) → severity → fix.

### F1 — No Stripe Tax on checkout (SEV-2, revenue/compliance)
**Standard:** Stripe recommends enabling `automatic_tax` on Checkout so tax is computed from the
customer's location and your registrations; it is a one-line addition for one-time payments.
([Collect tax with Checkout](https://docs.stripe.com/tax/checkout)). US sales-tax nexus on
digital SaaS is real once revenue concentrates in a state.
**Repo:** `billing/stripe_client.py:70-93` builds the session with no `automatic_tax` and no
`customer_update`/address collection. Tax is silently not collected → the business eats it or is
non-compliant once registered.
**Fix:** add `"automatic_tax": {"enabled": True}` + address collection once the business has at
least one tax registration; gate behind a config flag so dev/staging stay tax-free. **Needs a
DECISIONS entry** (tax posture is a business decision).

### F2 — No reconciliation between Stripe and the minute_packs ledger (SEV-2, silent revenue leak)
**Standard:** Stripe's guidance for any usage/credit system is to treat your DB as the source of
truth and **reconcile asynchronously** against Stripe, because webhooks can be missed or arrive
out of order ([Usage-based billing for AI](https://stripe.com/resources/more/ai-companies-and-usage-based-billing)).
**Repo:** fulfillment is **webhook-only** (`routers/billing.py:160-244`). If
`checkout.session.completed` is never delivered (Stripe outage, endpoint down past Stripe's retry
window), the customer **pays and gets no minutes**, and nothing detects it. There is no periodic
"Stripe says paid, ledger says ungranted" sweep.
**Fix:** a daily Beat task that lists recent Stripe Checkout sessions with `payment_status=paid`
and grants any missing `minute_packs` row (idempotent via the existing
`UNIQUE(stripe_session_id)`, `billing/ledger.py:62-68`). Belt-and-suspenders for the one path that
loses real money. **Needs no DECISIONS entry** (pure correctness).

### F3 — No money refund path; only automatic minutes-on-failure (SEV-3, UX/trust)
**Standard:** consumption-credit tools generally offer at least discretionary refunds; Stripe
refunds are first-class.
**Repo:** `billing/refund.py:36` refunds **minutes** (compensating ledger row) only on **terminal
ingest failure** (Issue 57). There is **no path** to refund *money* to a dissatisfied paying
creator, and no admin affordance. Acceptable for a beta, but a launch gap.
**Fix:** a documented manual refund runbook (Stripe dashboard refund + a compensating
negative-minutes ledger entry to keep the ledger truthful) for v1; an admin endpoint later.
**Needs a DECISIONS entry** (refund policy is a business decision).

### F4 — Webhook idempotency: CORRECT, but note one residual (SEV-4, informational)
**Standard:** webhooks are at-least-once; dedupe on event/session id
([Meters / idempotency guidance](https://www.buildmvpfast.com/blog/stripe-metered-billing-implementation-guide-saas-2026)).
**Repo:** **well done** — fast-path check (`routers/billing.py:217-222`), RLS stamp *before* the
idempotency query (`routers/billing.py:215`, with an explanatory comment about the SEV1 it fixed),
and a `UNIQUE(stripe_session_id)` backstop with SAVEPOINT race handling
(`billing/ledger.py:61-98`). The Stripe `Idempotency-Key` on checkout creation is also correct
(`billing/stripe_client.py:99-102`, Issue 106). Residual: the webhook **rate-limits per source IP**
(`routers/billing.py:161`) which is correct, but **does not verify the `payment_status` of the
session** before granting — a `checkout.session.completed` with `payment_status != "paid"` (e.g.
async payment methods that later fail) would grant minutes. Today all packs are card/one-time so
this is latent; worth a guard. **No DECISIONS entry needed.**

### F5 — Proration / metered billing: N/A today, blocker for any future subscription (SEV-N/A, forward-looking)
**Standard:** since Stripe API `2025-03-31.basil`, legacy usage records are gone — every metered
price needs a backing **Meter**, and the Billing Meters API is the 2026 way to bill AI usage
([Meters API](https://docs.stripe.com/api/billing/meter);
[Metered billing guide 2026](https://www.buildmvpfast.com/blog/stripe-metered-billing-implementation-guide-saas-2026)).
**Repo:** one-time payments only (`billing/stripe_client.py:4` "no subscriptions, no meters").
This is *correct for today's model*. **If** a hybrid base-sub-plus-overage tier is ever added
(see §5), it must use the **Billing Meters API** (not legacy usage records) and write usage to the
DB first, syncing to Stripe asynchronously with idempotent meter-event identifiers. Captured here
so it isn't re-discovered later. **A subscription tier would need a DECISIONS entry.**

### F6 — Dunning / Smart Retries: N/A today, mandatory before any recurring charge (SEV-N/A, forward-looking)
**Standard:** ~25% of subscription churn is involuntary (failed payments); Stripe Smart Retries +
a coordinated email cadence recover most of it
([Smart Retries](https://docs.stripe.com/billing/revenue-recovery/smart-retries);
[2026 dunning playbook](https://www.digitalapplied.com/blog/failed-payment-recovery-dunning-playbook-2026)).
**Repo:** none — there are no recurring charges to dun. Listed so that the moment a subscription
tier ships, Smart Retries + dunning emails are part of that issue, not an afterthought.

---

## 4. Quotas + cost control (the real margin guard)

**Finding (SEV-1):** there is **no per-creator usage quota or rate limit before an LLM/render
job**, only a balance *floor*. Today's guards:
- Render/clip endpoints gate on `check_positive_balance` (a >0 floor), not a ceiling
  (`routers/clips.py:206,385,575,685`).
- Upload deducts minutes idempotently per `video_id` (`billing/ledger.py:103`,
  `worker/tasks.py:521-524`) — **good**: one video = one deduction, retries don't double-charge.
- Chat has a daily message quota (`CHAT_DAILY_MESSAGE_LIMIT=25`, `config.py:76`; Issue 152) — the
  *only* operation with a real per-creator ceiling.

**The gap:** re-render, re-score, title/hook/thumbnail generation, and insight calls are **not
metered against minutes and have no per-creator daily/burst cap**. A creator (or a leaked
session) can call these in a loop. Each is cheap individually, but unbounded fan-out is exactly how
a consumption product goes underwater — and how an attacker burns your Anthropic/Deepgram bill.
The CLAUDE.md pre-launch checklist names this verbatim: *"Per-creator rate limiting + usage
quotas before each LLM/render job."*

**Recommended guardrail:** a small reusable per-creator quota layer (mirror the existing slowapi
`creator_key` pattern, `limiter.py`, already used at `routers/billing.py:75,128`) applied to
**every LLM and render endpoint** — a daily cap per operation class plus a short-window burst
limit — with the limits in `config.py`/`.env.example`. This is the single highest-ROI margin
change and a hard pre-launch gate.

---

## 5. Packaging analysis & recommendation

**Current:** 5 one-time packs, never-expiring, per-input-minute (`billing/packs.py:28-35`):
Starter 200min/$18 (9¢), Regular 500/$40 (8¢), Creator 1000/$70 (7¢), Pro 2000/$110 (5.5¢),
Studio 5000/$225 (4.5¢). No subscription (Issue 152 decision).

**Industry standard (2026):** per-input-minute credits are the **dominant** metering model for AI
clipping — Opus Clip (1 credit = 1 min, Starter $15/150min), Vizard, Klap, Captions all meter
input minutes ([COMPETITIVE_RESEARCH.md:51-63]; verified live pricing in that doc). The credit
economy's documented weakness is that **per-input-minute punishes 3–8h streams**
(`docs/COMPETITIVE_RESEARCH.md:38`).

**The contradiction to resolve:** `docs/COMPETITIVE_RESEARCH.md:113` recommends *avoiding*
per-input-minute and using *per-output-clip or flat-subscription*, directly opposing the shipped
model and the Issue 125 / Issue 152 DECISIONS. **Recommendation:** keep per-input-minute (it is
the category standard, matches the idempotent `minute_deductions` ledger keyed by `video_id`, and
Issue 152's reasoning against bolting a subscription onto a credit product is sound), **but**
close the stream-punishment gap two ways: (1) the existing per-minute **taper already does this**
(4.5¢ at Studio vs 9¢ at Starter) — make that rationale explicit in copy; (2) consider an explicit
**"Stream" pack** sized for multi-hour VODs. This keeps the honest, no-virality, channel-DNA value
prop intact while neutralizing the one real complaint. **Any pricing change needs a DECISIONS
entry** (the One Rule + the CLAUDE.md table both require it).

**Honesty check:** the live pricing copy is clean — `frontend/src/pages/Pricing.tsx:89-92`
carries the no-virality disclaimer and `:96-98,131-133` make the per-minute mechanic explicit. No
virality promise anywhere. The deleted `early-access.html` (which sold contradictory $29/$79
subscriptions against the minute model) is the cautionary tale logged in
`docs/OFF_COURSE_BUGS.md:30` and resolved in `docs/DECISIONS.md:87-88` — **do not reintroduce a
subscription funnel without a DECISIONS entry and a deliberate strategy call.**

---

## 6. Trial + activation tie-in

**Current:** new creators get a **60-minute free trial** (`billing/packs.py:29`) with a 7-day
window (`TRIAL_DURATION_DAYS=7`, `config.py:228`); `trial_ends_at` gates access
(`billing/ledger.py:173-189`), expiry has differentiated 402 copy (`billing/ledger.py:186-189`,
Issue 126), and a daily watchdog logs expiries (`worker/tasks.py` `expire_trials`).

**Finding (SEV-2, activation):** **60 trial minutes is generous enough to reach a first clip for a
10–60 min upload, but it is *time-boxed to 7 days* and there is no verification in the funnel that
the trial actually produces a finished clip before it lapses.** The grant covers the *minutes*; it
does not guarantee the creator reaches the aha-moment (a rendered, downloadable clip from their own
channel) within 7 days — which is the activation metric that converts. This overlaps prompt 07
(activation/onboarding) and should be owned jointly.

**Recommendation:** instrument **trial→first-clip→paid** conversion (the `event_logs` sink exists,
`docs/SOT.md:92`) and consider making the trial **outcome-based** ("your first clip is on us")
rather than purely time+minutes-boxed, so the credit grant is explicitly sized to one full
end-to-end clip. Defer the exact mechanic to prompt 07's funnel work; flag the data gap now.

---

## 7. Proposed issues (dependency-ordered, `docs/issues.md` house style)

> Pricing changes always need a `docs/DECISIONS.md` entry (per CLAUDE.md). Flagged per issue.

### Issue 171a — Per-creator pre-job usage quota + rate limit on every LLM/render endpoint
**What**: A reusable per-creator quota layer (extend the slowapi `creator_key` pattern in
`limiter.py`) applied to all render, re-render, scoring, and knowledge-generation
(title/hook/chapter/thumbnail/insight) endpoints — a daily cap per operation class + a short-window
burst limit, configurable in `config.py`/`.env.example`. Closes the CLAUDE.md pre-launch gate
"Per-creator rate limiting + usage quotas before each LLM/render job."
**Acceptance criteria**:
- [ ] Every LLM and render endpoint enforces a per-creator daily cap + burst limit before doing work
- [ ] Limits live in `config.py` and `.env.example` with descriptions
- [ ] Exceeding a cap returns a clean 429 with actionable copy (no stack trace)
- [ ] Test: a scripted loop against re-render is throttled; a normal session is unaffected
- [ ] No regression to upload-deduct idempotency
**DECISIONS entry**: not required (correctness/security), but log the chosen limits.

### Issue 171b — Stripe ↔ ledger reconciliation Beat task
**What**: A daily Celery Beat task that lists recent Stripe Checkout sessions with
`payment_status=paid` and grants any `minute_packs` row missing for a paid session (idempotent via
the existing `UNIQUE(stripe_session_id)`), alerting on any mismatch. Closes the webhook-only
fulfillment single-point-of-failure (F2).
**Acceptance criteria**:
- [ ] Beat task finds paid Stripe sessions with no corresponding granted `minute_packs` row and grants them
- [ ] Re-running the task is a no-op (idempotent; no double-grant)
- [ ] A persistent mismatch emits an alert/log (no PII, no Stripe secret)
- [ ] Test with a recorded Stripe fixture (no live API in CI)
**DECISIONS entry**: not required.

### Issue 171c — Verify `payment_status` before granting in the webhook
**What**: Guard `routers/billing.py` so `checkout.session.completed` only grants when
`payment_status == "paid"` (defends against async/delayed payment methods that complete the session
but later fail). Small, surgical (F4 residual).
**Acceptance criteria**:
- [ ] Webhook ignores a `completed` event whose `payment_status` is not `paid`
- [ ] Existing idempotency + RLS-stamp behavior unchanged
- [ ] Test covers paid vs. unpaid-completed events
**DECISIONS entry**: not required.

### Issue 171d — Stripe Tax on checkout
**What**: Add `automatic_tax` + address collection to the Checkout session
(`billing/stripe_client.py`), gated behind a config flag so dev/staging stay tax-free, enabled once
the business has ≥1 Stripe tax registration.
**Acceptance criteria**:
- [ ] Checkout computes tax from customer location when the flag is on
- [ ] Flag off (dev/staging) preserves current behavior exactly
- [ ] `.env.example` documents the flag and the registration prerequisite
- [ ] Test: session params include `automatic_tax` only when enabled
**DECISIONS entry**: **required** (tax posture is a business decision).

### Issue 171e — Money-refund runbook + truthful ledger entry
**What**: A documented manual refund process (`docs/RUNBOOKS.md`): Stripe-dashboard refund + a
compensating negative-minutes ledger entry so the `minute_packs` ledger stays truthful when a
creator is refunded money. (Admin endpoint deferred.)
**Acceptance criteria**:
- [ ] Runbook covers full and partial money refunds and the matching ledger correction
- [ ] Ledger remains append-only/immutable (compensating row, not mutation)
- [ ] Refund policy stated in user-facing copy
**DECISIONS entry**: **required** (refund policy is a business decision).

### Issue 171f — Packaging: explicit per-minute taper rationale + Stream pack (pricing review)
**What**: Resolve the docs contradiction (§5): formally keep per-input-minute, document the taper
rationale, and add/right-size a long-form **"Stream" pack**. Update `billing/packs.py`,
`frontend/src/pages/Pricing.tsx`, and reconcile `docs/COMPETITIVE_RESEARCH.md:113` with the
shipped model.
**Acceptance criteria**:
- [ ] Pack lineup + per-minute taper documented with the stream-punishment rationale
- [ ] `COMPETITIVE_RESEARCH.md` recommendation reconciled with the shipped model (no contradiction)
- [ ] Pricing copy still carries the no-virality disclaimer; no subscription reintroduced
- [ ] Per-minute prices verified against the §2 cost model (margin stays ≥ target floor)
**DECISIONS entry**: **required** (any pricing change).

### Issue 171g — Trial→first-clip→paid instrumentation (joint with prompt 07)
**What**: Instrument the activation funnel (`event_logs`) for trial start → first rendered clip →
first purchase; surface whether the 60-min/7-day trial reliably reaches a first clip before lapse.
Feeds the packaging/trial mechanic decision.
**Acceptance criteria**:
- [ ] `event_logs` captures trial-start, first-clip-rendered, first-purchase per creator
- [ ] A query/dashboard reports trial→first-clip and trial→paid conversion
- [ ] No PII/token in any logged event
**DECISIONS entry**: not required (instrumentation); a trial-mechanic change later would need one.

---

## 8. Open questions for the human (one-line answers)

1. **Packaging direction:** keep per-input-minute packs (category standard, matches the ledger),
   or pivot toward per-output-clip / a base-sub-plus-overage hybrid as `COMPETITIVE_RESEARCH.md:113`
   suggests? *(This resolves the doc contradiction and gates Issue 171f.)*
2. **Margin floor:** what minimum gross margin per video do we hold the cheapest pack to (the model
   shows ~80%+ today) — and what's the target after render compute is priced on real K8s nodes?
3. **Stripe Tax timing:** enable now (pre-registration, so it computes but you may owe) or only
   after the first state/country tax registration? *(Gates 171d.)*
4. **Refund policy:** discretionary money refunds (and within what window), or minutes-only /
   no-refund with the trial as the try-before-buy? *(Gates 171e.)*
5. **Trial shape:** keep 60 min + 7 days, or move to an outcome-based "first clip on us" grant
   sized to one full end-to-end clip? *(Gates 171g; overlaps prompt 07.)*
6. **Stream pack:** add an explicit long-form/stream pack sized for 3–8h VODs, or rely on the
   existing per-minute taper alone? *(Part of 171f.)*

---

## 9. Stale / contradictory docs flagged

- **`docs/COMPETITIVE_RESEARCH.md:113`** recommends avoiding per-input-minute credits and using
  per-output-clip / flat-subscription — **contradicts** the shipped model and `docs/DECISIONS.md`
  (Issue 125, Issue 152). Reconcile in Issue 171f.
- **`docs/research/findings/02_agentic_caching_cost.md`** is referenced as the LLM-cost feed for
  this prompt but **does not exist on disk yet** — the per-operation token table here should be
  reconciled against 02 once it lands (§2).
- **`docs/SOT.md:457`** still reads "Pricing / billing: Usage-based tiers… Research pending" — this
  brief is that research; SOT should be updated when Issue 171 closes.

---

## Sources

- [Deepgram pricing](https://deepgram.com/pricing) · [BrassTranscripts Deepgram breakdown](https://brasstranscripts.com/blog/deepgram-pricing-per-minute-2025-real-time-vs-batch)
- [Anthropic API pricing 2026 (Finout)](https://www.finout.io/blog/anthropic-api-pricing) · [pecollective Anthropic rates](https://pecollective.com/tools/anthropic-api-pricing/)
- [Voyage AI pricing](https://docs.voyageai.com/docs/pricing)
- [Cloudflare R2 pricing](https://developers.cloudflare.com/r2/pricing/)
- [Stripe Billing Meters API](https://docs.stripe.com/api/billing/meter) · [Stripe metered-billing guide 2026](https://www.buildmvpfast.com/blog/stripe-metered-billing-implementation-guide-saas-2026)
- [Stripe usage-based billing for AI](https://stripe.com/resources/more/ai-companies-and-usage-based-billing)
- [Stripe Smart Retries](https://docs.stripe.com/billing/revenue-recovery/smart-retries) · [2026 dunning playbook](https://www.digitalapplied.com/blog/failed-payment-recovery-dunning-playbook-2026)
- [Stripe Tax — Collect tax with Checkout](https://docs.stripe.com/tax/checkout) · [Stripe Tax docs](https://docs.stripe.com/tax)
