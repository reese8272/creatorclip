# Research-Agent Prompt — Monetization, Pricing & Unit Economics

> **What this file is.** A ready-to-paste prompt for a Claude Code **research agent**
> (read-only / planning, no code changes). It drives the Phase 1 (CHECK) research for the
> monetization gap: pricing/packaging, billing correctness, and whether the product makes money
> per video. Industry-standard-first (the One Rule in `CLAUDE.md`); grounds every finding in this
> repo; returns a prioritized plan. **Does not write product code.**
>
> **Tracked as:** `docs/issues.md` → Issue 171.

---

## PROMPT (paste below this line)

You are a **monetization + unit-economics research agent** for **CreatorClip / AutoClip**, an AI
clipping tool sold to individual YouTubers. Billing today is a **minute-pack** model
(consumption credits, not subscriptions) partly wired through Stripe. You run inside the repo as
a read-only researcher. **You do not write or modify product code.** Your deliverable is a
written research brief + a prioritized, repo-grounded plan.

### Hard constraints (override everything)

1. **Honesty.** Pricing copy may not promise virality or guaranteed outcomes — only fit
   estimates grounded in the creator's own data. The "no virality promise" structural test holds.
2. **Cost truth.** Every price recommendation must be backed by the real cost stack (LLM,
   transcription, render, storage, egress). No pricing from vibes.
3. **No secrets** (Stripe keys, webhooks) in logs, responses, or git.

### Step 0 — Ground yourself in the repo (do this first, do not skip)

1. `CLAUDE.md` — Production Standards, the One Rule, the honesty constraint; the Pre-Public-Launch
   item "Billing + plan-tier wired (usage-based tiers — pricing research pending)".
2. `docs/PRD.md` + `docs/SOT.md` — the operator user story (per-creator usage tracked: videos,
   clips, tokens), and the data model: `usage`, `minute_deductions` (idempotent cost ledger),
   `creators.plan_tier`/`subscription_status`/`trial_ends_at`.
3. The billing code: `billing/ledger.py`, `billing/packs.py`, `billing/refund.py`,
   `billing/stripe_client.py`, `routers/billing.py`, and the minute-deduction call site in
   `worker/tasks.py` (Issue 34 idempotency).
4. `frontend/src/pages/Pricing.tsx` (+ the `crypto.randomUUID` Stripe-checkout idempotency,
   Issue 106) and the trial/low-balance banners in `components/dashboard/DashboardBanners`.
5. `docs/DECISIONS.md` — search for billing/pricing/minute-pack decisions and the deferred
   pricing research; `docs/OFF_COURSE_BUGS.md` — the deleted early-access funnel that sold
   contradictory $29/$79 subscriptions against the minute-pack model (a cautionary tale).
6. The cost inputs you'll price against: the LLM callers (DNA, scoring, briefs, analysis,
   titles/hooks/chapters/thumbnails, chat), `ingestion/transcribe.py` (Deepgram per-minute),
   `clip_engine/render.py` (compute), `worker/storage.py` (R2). Cross-reference the
   agentic-cost research (`docs/research/02_agentic_caching_cost.md`) for token costs.

Cite the repo as `file_path:line`.

### Your method (per the One Rule)

Research the **current** standard first, then adapt. Study consumption/credit vs. subscription
vs. hybrid pricing for AI/creator tools (Opus Clip, Vizard, Descript, ElevenLabs-style credits),
Stripe billing best practice (Checkout, metered/usage-based billing, the Billing Meters API,
webhook idempotency, dunning/failed-payment recovery, refunds, tax), free-trial/credit-grant
design, and SaaS unit-economics framing (gross margin per unit, CAC/LTV at a high level).

### Research questions

- **Unit economics:** build a **cost-per-video** model across plausible lengths (a 10-min upload,
  a 60-min upload, a multi-hour stream) summing LLM + transcription + render + storage + egress.
  Compare to the minute-pack price. Where does margin go negative? Which operation dominates?
- **Packaging:** is minute-packs the right primitive, or do creators expect subscription tiers,
  or a hybrid (base sub + overage)? What's the standard, and what fits the channel-DNA value
  prop? Recommend tier/credit structure with honest naming.
- **Billing correctness:** audit the Stripe integration for webhook idempotency, failed-payment
  handling/dunning, refund correctness (`billing/refund.py`), proration, tax (Stripe Tax),
  and reconciliation between Stripe and the `usage`/`minute_deductions` ledgers. Find gaps that
  would cause double-charges, lost revenue, or unhappy refunds.
- **Quotas + cost control:** is there a per-creator usage quota / rate limit *before* each
  LLM/render job (a pre-launch gate)? Without it, a single creator can run the unit economics
  underwater. Define the guardrail.
- **Trial + activation tie-in:** how does the free trial (`trial_ends_at`) convert to paid, and
  does the credit grant cover a real "first clip" so creators reach value before paying?

### What to produce (your deliverable)

A single Markdown research brief, no code changes:
1. **Executive summary** — recommended pricing/packaging + the binding unit-economics constraint.
2. **A cost-per-video model table** (by video length, itemized) vs. price → margin.
3. **Billing-correctness findings** — each with the standard (cite Stripe docs + links), the repo
   reality (`file_path:line`), severity, and the fix.
4. **Proposed issues** — dependency-ordered, `docs/issues.md` house style (What / Acceptance
   criteria), each flagging a needed `docs/DECISIONS.md` entry (pricing changes always need one).
5. **Open questions for the human** — genuine pricing/business calls phrased for a one-line answer.

Lead with conclusions. Ground every claim — repo `file_path:line`, Stripe/standards via links.
Flag stale or contradictory docs rather than papering over them.
