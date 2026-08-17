# D09 — Billing and the money path

**Domain:** Stripe integration, minute ledger, refunds, LLM cost accounting, spend guard.
**Scope judged against:** ≤100-user private beta on one DigitalOcean VM (`docs/DECISIONS.md` 2026-06-26).
**Method:** read `billing/` end to end, `routers/billing.py`, `worker/tasks.py:1357-1370, 4622-4728`,
`worker/schedule.py`, `tests/test_usage_coverage.py`; cross-checked every structural claim against
`docs/DECISIONS.md` §A7 (13 billing entries) and `docs/AUDIT_KNOWN_ISSUES.md` §D before filing.
Sources dated 2026-08-17 where fetched.

---

## Verdict

The **ledger core is the strongest code in this repo** — SAVEPOINT + UNIQUE idempotency on both
directions of the balance, a transactional-outbox listener for notifications, auto-refund on terminal
failure, and an AST sweep (`tests/test_usage_coverage.py`) that structurally forbids an unbilled
Anthropic call site. The **boundary with Stripe is where the money path is still thin**: the app grants
entitlement from `metadata.pack_id` alone and never reads `amount_total`, so the ledger's `price_cents`
column is a copy of a catalog constant rather than money received — amount reconciliation is
structurally impossible, not merely absent. Two silent-loss paths remain open (delayed-notification
purchases; refunds/chargebacks), and — most importantly for an audit of *process* — the single defect
that kept billing dead for its entire life (webhook registered at a path the app does not serve) was
fixed by hand in the Stripe Dashboard and has **no mechanism preventing recurrence**.

---

## What the current standard actually is, with sources

### Stripe Checkout fulfillment (fetched 2026-08-17)

[`docs.stripe.com/checkout/fulfillment`](https://docs.stripe.com/checkout/fulfillment.md?payment-ui=stripe-hosted)
prescribes six steps for a `fulfill_checkout` function. Verbatim from the page:

> 1. Correctly handle being called multiple times with the same Checkout Session ID.
> 2. Accept a Checkout Session ID as an argument.
> 3. Retrieve the Checkout Session from the API with the `line_items` property expanded.
> 4. Check the `payment_status` property to determine if it requires fulfillment.
> 5. Perform fulfillment of the line items.
> 6. Record fulfillment status for the provided Checkout Session.

Two things this settles for this repo:

- **Keying idempotency on Checkout Session ID is the documented standard**, not a shortcut.
  `docs/AUDIT_KNOWN_ISSUES.md` §D lists "idempotency is per-`checkout.session.id`, not per-`event.id`"
  as a concern. It is not one. Stripe's own reference handler passes
  `event['data']['object']['id']` — the session ID — to `fulfill_checkout` for **both**
  `checkout.session.completed` and `checkout.session.async_payment_succeeded`, precisely so the two
  events converge on one dedupe key. `event.id` keying would *break* that convergence. Do not "fix" this.
- **The reference handler subscribes to two events, not one.** Verbatim:
  `if event['type'] == 'checkout.session.completed' || event['type'] == 'checkout.session.async_payment_succeeded'`.
  Plus: *"You might also want to listen for and handle `checkout.session.async_payment_failed` events."*

On delayed methods, the same page: *"Automatic fulfillment with webhooks is **required** if you sell
subscriptions or accept payment methods with delayed success notification, because their subsequent
state changes only after the Checkout Session completes"* and *"Delayed payment methods generate a
`checkout.session.async_payment_succeeded` event when payment succeeds later. The status of the object
is `processing` until the payment status either succeeds or fails."*
([payment-method notification table](https://docs.stripe.com/payments/payment-methods#payment-notification))

### Amount verification — the "two separate assertions" principle

Stripe's fulfillment page does not itself mandate an amount check (its reference stops at
`payment_status != 'unpaid'`). Current community/practitioner guidance does, and it is the
standard this repo's own incident log independently derived:

> "you should validate that required metadata like `userId` and `productId` are present on the
> checkout session, and you should **verify the `amount_total` matches your expected amount** before
> recording the purchase and fulfilling the order."
> — [Hookdeck, *Guide to Stripe Webhooks: Features and Best Practices*](https://hookdeck.com/webhooks/platforms/guide-to-stripe-webhooks-features-and-best-practices)

The generalised form — **"provider says paid" and "app granted entitlement" are two separate
assertions that must be independently checkable** — is written into this repo's own
`snag-taxonomy.md` §D-1 as the lesson from the 10-week outage. The relevant reading here is not
"add an `if`"; it is "the ledger must record what was *collected*, so the two assertions can be
compared after the fact."

### Idempotency keys

[`docs.stripe.com/api/idempotent_requests`](https://docs.stripe.com/api/idempotent_requests) — keys are
account-wide, ≤255 chars, 24h window, and must unambiguously identify one operation per account.
`billing/stripe_client.py:140-147` derives `checkout:{creator_id}:{intent_id}` with the tenant prefix
*for exactly this reason* and documents it. This is correct and above the common bar.

### Anthropic pricing and cache economics (claude-api skill, cached 2026-06-24)

| Model | Input $/MTok | Output $/MTok | Min. cacheable prefix |
|---|---|---|---|
| Claude Opus 5 (`claude-opus-5`) | $5.00 | $25.00 | **512 tokens** |
| Claude Sonnet 4.6 | $3.00 | $15.00 | 1024 tokens |
| Claude Haiku 4.5 | $1.00 | $5.00 | 4096 tokens |

Cache reads bill ~0.1×; 5-min writes 1.25×; **1-hour writes 2×**. From
[`prompt-caching.md`](https://platform.claude.com/docs/en/build-with-claude/prompt-caching.md):
*"The minimum is not monotonic across generations… Claude Opus 5 halves the Opus 4.8 minimum
(1024 → 512), so prompts previously too short to cache now create entries with no code change."*

`config.py:156-172` matches the price book **exactly** ($3/$15 Sonnet, $1/$5 Haiku, $5/$25 Opus,
`COST_CACHE_READ_MULTIPLIER=0.1`, `COST_CACHE_WRITE_MULTIPLIER=1.25`, `2.0` threaded for ttl:1h
callers). No drift. The comment on `config.py:164` says "Opus 4.8" where the configured model is
`claude-opus-5`, but the rates are identical so nothing is mispriced — a stale comment, not a defect.

---

## Findings

### F1 — Nothing verifies the Stripe-registered webhook endpoint resolves to a route this app serves
**Verdict: gap · Severity: high · Confidence: high**

`billing/webhook` was registered in Stripe as `/webhooks/stripe` while the app serves
`/billing/webhook` — a 404 for the endpoint's entire life (`docs/GO_LIVE.md:91`, Issue 485b). It was
fixed by editing the URL in the Stripe Dashboard. **Nothing in the repo prevents recurrence.**

- `grep -n webhook scripts/doctor.py` → **zero hits.** The preflight validates Stripe *auth* through
  the real client (`_live_stripe` was hardened for exactly this class after OFF_COURSE_BUGS:148) but
  never asks Stripe what endpoints are registered.
- `grep -rn webhook_endpoints scripts/ --include=*.py` → **zero hits.**
- The three tests that mention the path (`tests/test_issue_110.py:44-57`, `tests/test_static.py:1038`,
  `tests/test_billing*.py`) all assert properties of `routers/billing.py` **source or the app's own
  route table**. None of them can see Stripe's side of the contract, and none claims to.

The transport half of the 2026-08-12 incident got a structural fix; the routing half got a manual one.
This is the project's own named #1 failure mode (`snag-taxonomy.md` §E-4: *"repeatedly identifies the
correct structural fix and then does not make it structural"*) landing on the money path.

**Failure scenario.** Any future action that re-creates the endpoint — rotating the signing secret by
deleting and re-adding it, adding a second endpoint for a staging environment, a Dashboard-side
migration, or moving the router prefix — restores a 404 that is invisible from inside the app. Symptom
is identical to 2026-05-31 → 2026-08-12: `POST /billing/checkout` returns 200, the customer is charged,
`billing_webhook_received` never fires, and the only backstop is `reconcile_stripe_ledger` — which
would then be the sole fulfilment path, at up to 24h latency, permanently, with no signal that the
primary path is dead.

**The check is one API call.** `_STRIPE.webhook_endpoints.list()` returns `url` and `enabled_events`
for each endpoint. `doctor.py` can parse each URL's path, assert it is present in FastAPI's route
table, and assert `enabled_events` covers the set the handler branches on. That closes F1 and F2's
detection half in the same probe.

---

### F2 — A Dashboard toggle creates a take-money-grant-nothing path that the 48h sweep cannot catch
**Verdict: deviation-unjustified · Severity: high · Confidence: high**

Three independent facts compose into one silent-loss path:

1. **`billing/stripe_client.py:93-116` never sets `payment_method_types`.** The offered method set is
   therefore whatever is enabled in the Stripe Dashboard — a UI toggle, no code change, no deploy, no
   review.
2. **`routers/billing.py:245-260` handles only `checkout.session.completed`** and returns
   `{"status": "ignored"}` for anything else. `checkout.session.async_payment_succeeded` is named in
   the comment at `:256` and **is not a branch**. `async_payment_failed` is not mentioned at all.
3. **The reconcile fallback filters on session *creation* time, not settlement time.**
   `billing/stripe_client.py:173` computes `cutoff_ts = now - STRIPE_RECONCILE_LOOKBACK_HOURS` and
   `:180` passes it as `"created": {"gte": cutoff_ts}`. `STRIPE_RECONCILE_LOOKBACK_HOURS = 48`
   (`config.py:881`), justified in `.env.example:299` as *"48h covers one missed Beat run + Stripe
   retry window"* — which is correct reasoning for a *card* payment and wrong for a delayed one.

**Failure scenario with real numbers.** Owner enables ACH Direct Debit in the Stripe Dashboard (a
plausible move — it is the cheapest method for a $400 Stream pack). A creator buys Stream ($400.00,
10,000 minutes) on day 0. Checkout completes; `checkout.session.completed` fires with
`payment_status: "unpaid"`; `routers/billing.py:259` returns `{"status": "ignored"}` — correctly, per
Issue 206. ACH settles on day 4; `checkout.session.async_payment_succeeded` fires; **no branch handles
it**. The daily sweep on day 4 queries `created[gte] = day 2`; the session was created on day 0 and is
outside the window. The sweep on every subsequent day is further outside it. Net: **$400 collected,
0 minutes granted, permanently, with no error anywhere** — `errors` in
`worker/tasks.py:4717-4728` never increments because the session is never returned by the list call.

`docs/AUDIT_KNOWN_ISSUES.md` §D already names the unhandled event. The new signal is the other two
legs: that the trigger is a **Dashboard toggle rather than a code change**, and that the reconcile
fallback — the thing that would otherwise make the missing branch survivable — is keyed on the wrong
timestamp and cannot catch it.

**Two independent fixes, both small.** (a) Pin `payment_method_types: ["card"]` so the code and the
Dashboard cannot drift, *or* add the two async branches. (b) Filter the sweep on
`status=complete` + `payment_status=paid` without a `created` bound for a wider window (e.g. 14d),
or drop the `created` filter and page until `created < cutoff` — the loop at
`billing/stripe_client.py:205-208` already has that break, and it is currently dead code because the
server-side `created[gte]` guarantees it never fires.

---

### F3 — The ledger records the catalog price, never the amount collected, so amount reconciliation is structurally impossible
**Verdict: deviation-unjustified · Severity: medium · Confidence: high**

`routers/billing.py:306-314` calls `grant_minutes(..., price_cents=pack.price_cents)`.
`billing/ledger.py:302` writes that value to `MinutePack.price_cents`. `cs["amount_total"]`,
`cs["currency"]`, and `cs["amount_subtotal"]` are read **nowhere in the repo**.

`docs/AUDIT_KNOWN_ISSUES.md` §D frames this as "not exploitable today" plus "a ledger-accuracy gap,"
and is right on the first half — `allow_promotion_codes` is unset, so Checkout offers no discount path.
But the second half is understated. `MinutePack.price_cents` is not an inaccurate record of revenue;
it is **a copy of a compile-time constant**, so no query over the ledger can ever disagree with the
catalog, and no reconciliation against Stripe's balance or payout report is possible on amount. The
answer to "did we collect what we billed?" is unanswerable by construction, not merely unanswered.

**Failure scenario with real numbers.** The owner creates a Payment Link in the Stripe Dashboard for a
$1.00 promo and — following the metadata convention visible in their own prod logs — sets
`metadata[creator_id]=<uuid>` and `metadata[pack_id]=stream`. Payment Links emit
`checkout.session.completed` to the same endpoint (per the fulfillment page: *"Payment Links use
Checkout, so all of the information below applies to both"*). The handler reads `pack_id="stream"`,
looks up `Pack("stream", "Stream", 10000, 40000)` (`billing/packs.py:61`), and grants **10,000
minutes** while writing **`price_cents=40000`**. A $1.00 payment books as $400.00 of revenue and
10,000 minutes of compute — a 400× over-grant with a ledger row that will never contradict it.

This needs Dashboard/API-key access, so it is not an unauthenticated exploit; it is an
own-goal-shaped defect. But the ledger-accuracy half needs no attacker at all.

**Caution for the fix (judgement call).** A naive `amount_total != pack.price_cents → reject` will
false-positive if Stripe Tax is ever flipped on (`STRIPE_TAX_ENABLED`, `stripe_client.py:136-138`
adds tax *on top* of `unit_amount`) or if Stripe Adaptive Pricing converts the presentment currency.
Compare `amount_subtotal` against `price_cents` **with a `currency == "usd"` guard**, and store
`amount_total` + `currency` on `MinutePack` regardless — the stored columns are the part that makes
reconciliation possible and they carry no false-positive risk.

---

### F4 — Refunds and chargebacks are entirely unhandled, in both directions
**Verdict: gap · Severity: medium · Confidence: high**

`grep -rn "charge\.\|dispute\|payment_intent" --include=*.py routers/ worker/ billing/` returns no
handler for `charge.refunded`, `charge.dispute.created`, `charge.dispute.funds_withdrawn`, or
`payment_intent.payment_failed`. Every occurrence of "refund" in the codebase is the *minute*-refund
path (`billing/refund.py`, ingest failure), not a money event.

`docs/DECISIONS.md:4141` (Issue 208) is a recorded decision that money refunds are
"discretionary, ledger-append-only, no admin endpoint at launch," with the procedure in
`docs/RUNBOOKS.md`. **That decision covers refunds this business *initiates*. It says nothing about
refunds and disputes Stripe *reports*** — which is the case that costs money. This is a gap in the
decision, not a deviation from it.

**Failure scenario with real numbers.** A creator buys Stream ($400.00 / 10,000 minutes), consumes
8,000 minutes over three weeks, then files a card chargeback. Stripe withdraws $400 plus the dispute
fee. The app: `MinutePack` row stays, `Creator.minutes_balance` keeps the remaining 2,000 minutes, the
creator keeps rendering, and **no code path anywhere observes the event**. At beta scale one such
event is the entire month's revenue.

The reverse-direction gap is the same shape: `_reconcile_stripe_ledger_async` sweeps
Stripe→ledger only (grant anything Stripe says was paid that the ledger lacks). It never sweeps
ledger→Stripe, so a `MinutePack` row whose Stripe session was refunded, disputed, or never existed is
invisible. Handling `charge.refunded` / `charge.dispute.created` by inserting a compensating
`money_refund:*` row (the pattern `billing/refund.py:18-26` already documents) plus a
balance decrement closes both.

---

### F5 — The prompt-cache floor is hardcoded to Sonnet 4.6's 1024, but the three most expensive calls run Opus 5, whose floor is 512
**Verdict: deviation-unjustified · Severity: medium · Confidence: high**

Two constants gate the `ttl:"1h"` cache marker:

- `knowledge/util.py:36` — `_CACHE_FLOOR_TOKENS: int = 1024`, applied at `:77` as
  `(len(static_text) + len(block["text"])) // 4 >= _CACHE_FLOOR_TOKENS`
- `clip_engine/scoring.py:103` — `_CACHE_FLOOR_CHARS: int = 4 * 1024`, applied at `:403` as
  `combined_chars // 4 >= 1024`

Both are documented against **Sonnet 4.6** (`scoring.py:98-99`: *"Minimum combined prefix size (chars)
required to clear Sonnet 4.6's 1024-token cacheable-prefix floor"*; `util.py:61-62` the same). That was
correct when Issue 315 set it (`docs/DECISIONS.md:2837`, 2026-06-24, which supersedes all 2048 refs and
is right for Sonnet 4.6). It was not revisited when the 2026-08-05 wave (`docs/DECISIONS.md:921` §6)
moved `ANTHROPIC_MODEL_SCORING`, `ANTHROPIC_MODEL_VIDEO_CONTEXT`, and `ANTHROPIC_MODEL_CLIP_METADATA`
to `claude-opus-5` (`config.py:117,141,145`). **Opus 5's minimum cacheable prefix is 512 tokens** —
Anthropic halved it from Opus 4.8's 1024, and the docs call this out explicitly as the case where
"prompts previously too short to cache now create entries with no code change."

**Failure scenario with numbers.** A creator with a compact DNA brief produces a combined
static-corpus + DNA prefix of ~700 estimated tokens (~2,800 chars). `scoring.py:403` computes
`2800 // 4 = 700 >= 1024` → **False** → no `cache_control` is attached. Every
`score_candidates` call for that creator pays full $5/MTok input on ~700 tokens of prefix that Opus 5
would have served at $0.50/MTok. Per call that is ~$0.0032 forgone; the loss is small per call and
100% of the achievable saving on that block, and it applies to the highest-volume, highest-cost call
in the system — one per generate-clips run, plus the same gate on `video_context` and
`clip_metadata`. The gate is silently conservative: the code cannot tell the difference between
"cached" and "declined to try," and `cached_write_1h` (`scoring.py:455`) confirms the marker *landed*,
not that it *should have been sent*.

The deeper issue is the shape: a **single global constant** encoding one model's floor, in a codebase
whose stated architecture is a 20-entry per-task model registry with `tests/test_model_config.py`
banning hardcoded model literals. The floor should be derived from the configured model for the call
site, the way `billing.ledger.model_rates()` already derives per-model pricing. Note also that
`chars // 4` is a Sonnet-calibrated heuristic, and Opus 5 uses the Opus 4.7-generation tokenizer,
which tokenizes the same text differently — so the estimate and the threshold are both mis-tuned in
the same direction.

---

### F6 — The spend guard fails open correctly, but its failure is unobservable, and Redis is a single point of failure for every cost control at once
**Verdict: gap · Severity: medium · Confidence: high**

The fail-open posture itself is **right** and matches `flags.py` (`spend_guard.py:36-37`: *"the spend
guard being down must never take LLM features down with it"*). I am not filing that. Three things
around it are the finding:

1. **Failure is a once-per-process log line.** `_warn_fail_open` (`spend_guard.py:112-119`) guards on a
   module-global `set`, so a Redis flap warns once per worker for the process's lifetime. There is no
   counter, no `log_event`, no `record_event` — the durable-DB rail that `_emit_spend_event` uses for
   every *breach* is not used for the guard being *down*.
2. **The observability rails that would carry it are dark.** `/metrics` auto-disables in production
   when `METRICS_TOKEN` is unset (`config.py:1144-1150`), and it *is* unset (OFF_COURSE_BUGS:128).
   `grep alert docs/dashboards/` returns zero hits — there are no alert rules anywhere in the repo.
   So even a counter would have no consumer today.
3. **Redis is the shared dependency of every cost control.** `spend_guard.record_spend`,
   `creator_block_status`, and slowapi's per-creator daily quota all sit on the same Redis. `limiter.py`
   documents the interim fix explicitly: *"Under the bounded socket-timeout fallback above, a Redis
   stall degrades to fail-open and the cap is not enforced"* (`limiter.py:148-149`). One Redis outage
   removes the per-creator daily cap, the global daily/monthly caps, the velocity breaker, **and** the
   stacked rate limits, simultaneously and silently.

**Worst-case hour with the guard down, at current pricing.** The Opus 5 chain per video is roughly
video-context (~20k in / 2k out → $0.15) + `score_candidates` over ≤18 candidates (~25k in / 4k out →
$0.23) + batched clip metadata (~10k in / 3k out → $0.13), plus Sonnet-tier titles/hooks/chapters
(~$0.05–0.15) — call it **~$0.60 per video**. The general Celery queue runs `--concurrency=4`
(`docker-compose.prod.yml:46`), the render queue `--concurrency=1` (`:72`), so the physical ceiling is
4 concurrent pipelines; at 5–10 min wall clock each that is ~24–48 videos/hour.

**→ ~$15–30/hour.** Against `SPEND_CAP_GLOBAL_DAILY_USD = 50` and
`SPEND_CAP_GLOBAL_MONTHLY_USD = 400` (`config.py:977-978`): a saturated queue with the guard down
exceeds the **daily** cap in ~2–3 hours and the **monthly** cap in under a day.

The honest reading: this is **bounded, not catastrophic** — worker concurrency is the real backstop,
and at 100 beta users a saturated queue is itself implausible. The finding is not the dollar figure,
it is that the guard can be non-functional for weeks with no signal — the same shape as the
`health-check.yml` schedule that "silently died 2026-06-17 and nobody noticed." A single
`SPEND_GUARD_FAILOPEN_TOTAL` counter plus one `record_event` on first fail-open makes it detectable
the moment `/metrics` has a scrape target.

---

### F7 — `OFF_COURSE_BUGS.md:70` (cached tokens billed 0×) is FIXED and has been stale for ~8 weeks; the audit's own ground truth inherited the stale claim
**Verdict: aligned · Severity: low · Confidence: high**

The row states `_estimate_cost_usd` "prices only `input_tokens`+`output_tokens`" and that
`COST_CACHE_READ_MULTIPLIER=0.1` "is referenced nowhere." Both statements are false against this tree:

- `billing/ledger.py:148-153` prices all four tiers:
  `cache_read_tokens * cost_per_mtok_in * settings.COST_CACHE_READ_MULTIPLIER` and
  `cache_creation_tokens * cost_per_mtok_in * cache_write_multiplier`.
- `record_llm_usage` (`:231-233`) threads `cache_read` / `cache_creation` from the usage dict and
  accepts `cache_write_multiplier`; the ttl:1h callers pass `2.0`
  (`worker/tasks.py:653`, `:812`, `routers/clips.py:2323`).
- The two call sites the row names as "the biggest under-bills" are both fixed:
  `chat/runner.py:196-203` passes `cache_read`/`cache_creation` into `_estimate_cost_usd`; the
  scoring path prices them in-function.

The fix shipped the same day the row was written (`docs/DECISIONS.md:3007`, 2026-06-24 — *"LLM cost
ledger now prices cached tokens (read 0.1×, write 1.25×/2×)"*). The row has sat
`📋 Open — awaiting approval (money path)` ever since. `snag-taxonomy.md` §A class 8 lists it as a live
money-path leak, so the stale record propagated into this audit's own ground truth.

**Revenue impact of the (already-fixed) bug: zero going forward.** Its remaining significance is as
evidence for `snag-taxonomy.md` §E-3 — the off-course log has no closing pressure, and a *money-path*
row is the worst possible one to leave stale, because downstream readers reasonably assume money rows
get triaged first.

One residual, sub-severity: `chat/runner.py:196` calls `_estimate_cost_usd` without
`cache_write_multiplier`, so it defaults to 1.25×. If chat ever attaches a `ttl:"1h"` marker, its cache
writes under-bill by 0.75× on the write tokens only. Worth a one-line check, not an issue.

---

## What is genuinely right here

- **`billing/ledger.py` idempotency is structural, not conventional.** `grant_minutes` and
  `deduct_for_video` are mirror images: fast-path SELECT → `begin_nested()` SAVEPOINT → `flush()` to
  force the INSERT and surface the UNIQUE conflict *now* → `IntegrityError` → clean no-op. The
  `deduct_for_video` balance update is a single conditional
  `UPDATE … WHERE minutes_balance >= minutes RETURNING` — no read-modify-write, no race. And
  `grant_minutes:314-320` correctly **re-raises** `IntegrityError` when `stripe_session_id is None`,
  because a non-keyed grant has no UNIQUE to race on and swallowing it would silently give a new beta
  user 0 trial minutes (Issue 76). That distinction is the difference between an idempotency handler
  and a bug-swallower, and it is explained in the code.
- **The `after_commit` / `after_rollback` listener pair (`ledger.py:37-67`) is a correct
  transactional outbox.** `balance_low` notifications are staged in `session.info` and drained only on
  commit, with a rollback handler that discards them — closing the "notified for a deduction a later
  rollback undid" hole. Class-level listener, so no per-call bookkeeping.
- **`tests/test_usage_coverage.py` is the best gate in the repo.** It is a **bidirectional** AST sweep:
  `discovered - mapped` fails on a new unbilled Anthropic call site, and `mapped - discovered` fails on
  a stale map entry — the staleness check is what most such gates omit and is why this one cannot rot
  into a vacuous pass. Layer 2 was written *because* layer 1 (per-function grep in `worker/tasks.py`)
  could not see `chat/intake.py` and `knowledge/thumbnails.analyze_thumbnail_patterns`, and the
  evidence marker for the latter (`"_patterns_usage,"`) is deliberately narrowed so the sibling
  concepts-billing line cannot satisfy it. That is a gate whose author understood how gates fail.
- **`model_rates()` falls back to Opus rates for unknown model families** (`ledger.py:189-190`) —
  the *highest* price in the book — with the reasoning stated: *"so a misconfigured model never
  under-bills against the spend guard."* Failing expensive rather than cheap on the money path is the
  right default and is rarely gotten right.
- **`record_llm_usage` orders its two rails correctly.** The Prometheus counter + spend-guard tap run
  in the first try block; the DB ledger write runs in a second. The spend guard therefore survives a
  DB failure, which is the failure mode that matters for cost control.
- **The Stripe idempotency key is tenant-prefixed** (`stripe_client.py:140-147`) with the account-wide
  key-collision hazard spelled out. Most integrations pass the bare client UUID.
- **`spend_guard._record_and_enforce` releases the trip latch when `_flip_llm_flag` raises**
  (`:346-353`) — *"A latch with no flip would silence the breaker for the full cool-down TTL while the
  breach keeps burning."* That is the failure mode a naive SETNX latch has, correctly anticipated.
- **The per-creator arm can never trip the global switch** (`:299-317`). One creator's runaway spend
  gets a cool-down key; only the global/velocity arms touch `llm_generation`. Explicit, and right.
- **The `payment_status == "paid"` narrowing is correct and documented.** Stripe's reference uses
  `!= 'unpaid'` (which also fulfils `no_payment_required`); `routers/billing.py:250-259` narrows to
  `== 'paid'` and states why — every `PURCHASABLE_PACKS` entry has `price_cents > 0`, so
  `no_payment_required` is not a valid outcome. Tighter than the reference, for a stated reason.
- **Webhook rate limiting sits in front of signature verification** (`:220-239`) so a bad-signature
  flood cannot burn worker threads on HMAC. Correct ordering, and rarely done.
- **The `packs.py` docstring is honest about its own drift risk** (`:22-26`): it names
  `Pricing.tsx`'s duplicate `PACKS` const, says the right fix is to drive it from `/billing/packs`, and
  says why that was out of scope. That is the disclosure standard the rest of the repo should hold to.

---

## Decisions this domain needs but does not have

1. **A single "money-path assertions" entry.** The 10-week outage produced the correct lesson —
   *provider says paid* and *app granted entitlement* are two independently checkable assertions — but
   it lives in `snag-taxonomy.md` and `GO_LIVE.md:91` prose, not in `docs/DECISIONS.md`. `§A7` has 13
   billing entries and none states the principle, so each new money surface re-derives it (or doesn't).
2. **Which Stripe events this app subscribes to, and why the others are excluded.** Currently the
   answer is implicit in one `if` at `routers/billing.py:245`. It should be an explicit list —
   `checkout.session.completed` plus the async pair plus the refund/dispute set — with the ones
   deliberately unhandled named as deliberate.
3. **Whether the Stripe payment-method set is code-controlled or Dashboard-controlled.** Today it is
   Dashboard-controlled by omission (F2). Either position is defensible; the undeclared one is not.
4. **What `MinutePack.price_cents` means.** Catalog price or amount collected? It currently means the
   former while reading like the latter (F3). One sentence settles it and dictates whether
   `amount_total`/`currency` become columns.
5. **A reconciliation objective.** `reconcile_stripe_ledger` exists (Issue 205) but no entry states
   what divergence it is supposed to detect, in which direction, at what latency, or who is told. As
   built it is a Stripe→ledger *repair* job, not a *detector* — the `errors` counter at
   `worker/tasks.py:4722-4728` writes `logger.error` into a log with no alert rule and no scrape
   target. "What notices if a webhook is missed entirely?" has an honest answer today: **nothing
   notices; the sweep silently repairs it within 24h if it falls inside 48h of session creation, and
   silently does not if it doesn't.**
6. **A cache-floor-per-model policy.** `config.py` holds a 20-entry model registry and
   `tests/test_model_config.py` bans hardcoded model literals — but the cacheable-prefix floor is a
   hardcoded global in two files (F5). Either derive it from the configured model (mirroring
   `model_rates()`), or record why one conservative floor is preferred.
7. **A cost-of-goods position beyond Anthropic.** `record_llm_usage` is a genuinely excellent choke
   point for *Anthropic* spend. Deepgram (`COST_PER_MIN_DEEPGRAM = 0.0097`, `config.py:179`), Voyage
   (`COST_PER_MTOK_VOYAGE`, `:182`), R2 egress, and ffmpeg CPU are all priced in config and **none is
   recorded in the ledger or visible to the spend guard**. At the Stream pack's 4.0 ¢/min, Deepgram
   alone is ~24% of revenue per minute — the margin model exists in `packs.py`'s docstring but nothing
   measures it. This is not urgent at 100 users, but "the spend guard covers Anthropic only" should be
   a written scope boundary rather than an unexamined one.
8. **When a `📋 Open` money-path row gets force-triaged.** F7 shows a money row sitting stale for
   ~8 weeks after its fix shipped, and propagating a false claim into this audit's ground truth. A rule
   as blunt as "money-path rows are re-verified at every close-out" would have caught it.

---

## Explicitly checked and NOT filed

- **`stripe.HTTPXClient`.** `billing/stripe_client.py:40-55` uses `RequestsClient` deliberately, with
  both defects documented in place. Correct; do not touch.
- **`event.id`-based idempotency.** Stripe's own reference keys on Checkout Session ID for exactly the
  reason this repo does. `AUDIT_KNOWN_ISSUES.md` §D's concern here is misplaced (see Standard, above).
- **`record_llm_usage` swallowing exceptions** (`ledger.py:253`). Named in `AUDIT_KNOWN_ISSUES.md` §D.
  Best-effort is the right posture for a ledger write that must not break a pipeline, and the spend
  guard runs *before* it so cost control survives a DB failure. Already known; not new signal.
- **Redelivery double-spend.** The prompt lists this as open; `docs/issues.md:117` records it closed —
  *"Redelivery is a no-op (PK check-then-insert; no double spend)"* — and `UNIQUE(video_id)` on
  `MinuteDeduction` plus the SAVEPOINT in `deduct_for_video` back that up structurally. Verified closed.
- **The `list_recent_paid_sessions` dead break at `stripe_client.py:205-208`.** `oldest_created <
  cutoff_ts` can never be true while the server-side `created[gte]` filter is set. Harmless today, and
  it becomes live code the moment F2's fix removes that filter. Noted, not filed.
- **`config.py:164`'s "Opus 4.8" comment** on rates used for `claude-opus-5`. Rates are identical
  ($5/$25); stale comment, no mispricing.
