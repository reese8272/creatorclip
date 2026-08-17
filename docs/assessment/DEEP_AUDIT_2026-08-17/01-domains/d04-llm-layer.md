# D04 — LLM layer: models, caching, cost, evals, resilience

**Domain researcher output, Phase 1 of the deep standards audit. 2026-08-17.**
Read-only pass over the 17 Anthropic call sites, `config.py` model registry, the caching
implementation, the golden/live eval lanes, and the resilience posture. Sources dated 2026 below.

---

## Verdict

This is the strongest domain in the repo and it is at or above current industry standard on the
things that usually go wrong: one billing choke point enforced by a repo-wide AST sweep,
prompt-injection defense that matches OWASP LLM01:2025, floor-gated caching that measures rather
than assumes, and a scoring golden lane that replays real recorded API bodies and **fails when the
configured model changes**. The gaps are not in what was built — they are that the *guards* around
it are hand-maintained registries that have already drifted (one real defect is live behind a green
suite), that nothing anywhere records which model actually served a response, and that the one
genuine quality instrument the project owns emits its signal to a page nobody reads. A circuit
breaker is **not** what this system needs at ≤100 users; a written position and a receiver for the
existing alarm are.

---

## What the current standard is, with sources

**Model deprecation / upgrade.** The 2026 consensus is that model IDs live in config (not source),
and that the eval suite runs continuously against the **n+1 candidate** alongside the pinned model,
so the migration delta is a known quantity weeks before a sunset window closes rather than a
discovery on day 28 of 60. Anthropic ships a public deprecation calendar with dated retirements
(e.g. `claude-opus-4-20250514` deprecated 2026-04-14, retires 2026-06-15).
— [The Model Deprecation Treadmill (2026-04-27)](https://tianpan.co/blog/2026-04-27-model-deprecation-treadmill-pre-sunset-discipline),
[AI Model Deprecation and Lifecycle Calendar](https://hidekazu-konishi.com/entry/ai_model_deprecation_and_lifecycle_calendar.html)

**Prompt versioning / regression.** A `prompt_version` id on both the trace and the eval row is the
join key between CI and production scoring — "without it, *the baseline* is a hand-wave." Every PR
touching a prompt, a model version, or a retrieval config triggers an eval run against a versioned
golden set; regression past a threshold blocks merge. Practical target is 100–300 paired cases per
route before a gate stops false-alarming.
— [Prompt Regression Testing: A Practical 2026 Guide](https://futureagi.com/blog/prompt-regression-testing-2026/),
[Golden dataset evaluation (Langfuse)](https://langfuse.com/resources/engineering/golden-dataset-evaluation),
[CI/CD quality gates that actually run (Galtea)](https://galtea.ai/blog/automated-llm-evaluation-building-a-ci-cd-quality-gate-that-actually-runs)

**Output-quality SLO / drift.** The standard shape is: sample a subset of production interactions,
score with a reference-grade judge model, **track the judge's score distribution over time**, and
alert when the mean drops below a threshold or the failing-response rate exceeds a percentage. The
distribution-over-time part is the SLO; a one-shot pass/fail is not.
— [9 Best LLM Drift Monitoring Platforms in 2026 (Galileo)](https://galileo.ai/blog/best-llm-output-drift-monitoring-platforms),
[AI Monitoring in Production 2026](https://valuestreamai.com/blog/ai-monitoring-in-production-guide-2026)

**Resilience.** Layered: retries for transient errors → fallback chain (alternate model → cached
response → explicit degradation message) → circuit breaker to stop hammering. Community-consensus
tuning is ~5 failures to trip, 60s cooldown, alert >5% error rate. The load-bearing caveat: "every
reliability layer must itself have a fallback."
— [Retries, fallbacks, and circuit breakers in LLM apps (Portkey)](https://portkey.ai/blog/retries-fallbacks-and-circuit-breakers-in-llm-apps/),
[Graceful Degradation Patterns in AI Agent Systems (2026-02-20)](https://zylos.ai/research/2026-02-20-graceful-degradation-ai-agent-systems/)

**Prompt caching — the current numbers** (fetched live from
`platform.claude.com/docs/en/build-with-claude/prompt-caching`, 2026-08-17). The minimum cacheable
prefix is **per-model and non-monotonic**:

| Model | Min cacheable prefix |
|---|---:|
| Claude Opus 5 / Fable 5 / Mythos 5 | **512** |
| Claude Opus 4.8 · Sonnet 5 · Sonnet 4.6 · Sonnet 4.5 | 1,024 |
| Claude Opus 4.7 · Haiku 3.5 | 2,048 |
| Claude Opus 4.6 · Opus 4.5 · **Haiku 4.5** | 4,096 |

Multipliers: 5-min write **1.25×**, 1-hour write **2×**, read **0.1×**. Critically: *"Requests below
the minimum token count are processed without caching, and **no error is returned**."*

---

## Findings

### 1. The LLM conformance registry has drifted, and one real defect is live behind it — HIGH

`tests/test_llm_conformance.py:34` (`_LLM_MODULES`) is a **hand-listed registry of 13 modules**. The
repo has **17** module-level `AsyncAnthropic` clients. Missing: `analysis/video_context.py`,
`knowledge/clip_metadata.py`, `preference/style_distill.py`, `routers/insights.py` — i.e. exactly the
newest additions (the two Opus 5 L26 calls, style distill, and the router-level client).
`tests/test_model_config.py:15` (`_TASK_MODEL_KEYS`) has the same shape: **14 of the 20**
`ANTHROPIC_MODEL_*` settings in `config.py`, missing `STYLE_DISTILL`, `VIDEO_CONTEXT`,
`CLIP_METADATA`, and the base `ANTHROPIC_MODEL`.

The gap is not hypothetical. `preference/style_distill.py:31`:

```python
_ANTHROPIC = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
```

No `timeout`, no `max_retries` — inheriting the SDK's **10-minute default**. That is precisely the
property `test_singleton_has_timeout_and_max_retries` exists to enforce, and the suite is green
because the module isn't in the list. `tests/test_sdk_timeouts.py` covers **3** of the 17 clients
(`dna.brief`, `improvement.brief`, `clip_engine.scoring`).

`docs/AUDIT_KNOWN_ISSUES.md:226` states the untrusted-content policy is *"pinned by structural
tests"* — those four modules do all inject it, verified by grep, so no security hole today. But the
pin does not cover them, so the claim is stronger than the mechanism.

**Failure scenario:** Anthropic stalls on the style-distill request. The client waits up to 600s.
The only backstop is Celery `soft_time_limit=120` on `distill_style_prefs` (`worker/tasks.py:1573`),
so the failure lands as `SoftTimeLimitExceeded` raised at an arbitrary point inside httpx rather
than a clean `APITimeoutError` — it is not classified by the module's
`except (RateLimitError, APIStatusError, APIConnectionError)` handler, holds one of two worker slots
for the full 120s, and is not retried with backoff the way a typed API error would be.

**The fix already exists in this repo.** `tests/test_usage_coverage.py` layer 2 solves the identical
problem the right way: a repo-wide AST sweep that discovers every `*.messages.create` /
`*.messages.stream` call site and fails on any site not present in an explicit map. Applying that
sweep to conformance is ~30 lines and permanently closes the class.

---

### 2. Nothing anywhere records which model actually served a response — HIGH

`response.model` — the **resolved** model on every Anthropic response — is read at zero call sites.
`record_llm_metric(model: str, ...)` (`observability.py:254`), `record_llm_usage`
(`billing/ledger.py:193`), and every token log line take the *requested* string from
`settings.ANTHROPIC_MODEL_*`.

`docs/DECISIONS.md:~11983` (Issue 318) deliberately chose **bare rolling aliases** over dated
snapshots, and reasoned it well: *"date-suffix pins to a specific snapshot that Anthropic may
deprecate, requiring a code change."* Correct. But the entry considers only the deprecation risk,
not the reciprocal one: **a rolling alias re-points by definition.** `claude-haiku-4-5` has snapshot
`claude-haiku-4-5-20251001`; when a new snapshot ships under the same alias, hooks, chapters,
performer analysis and style distillation change behavior with no code change, no deploy, and no
artifact anywhere in the system recording that it happened.

**Failure scenario:** Anthropic re-points `claude-sonnet-4-6` to a newer snapshot. The 11 Sonnet
tasks change behavior overnight. `llm_tokens_total{model="claude-sonnet-4-6"}` and every
`llm_usage` ledger row still carry the old string, so cost-per-feature dashboards show a step
change with no attributable cause; the scoring goldens (which pin `ANTHROPIC_MODEL_SCORING`'s
*alias*, `tests/test_scoring_goldens.py:113`) stay green; and if a creator disputes a bad clip
there is no record of which model produced it. One-line fix: pass
`getattr(response, "model", requested)` into the metric/ledger, and log it beside the token line
that already exists at `clip_engine/scoring.py:466`.

This is the missing half of the deprecation policy: the recorded decision covers *what to pin*, and
nothing covers *how you would notice it moved*.

---

### 3. The prompt-cache floor is a constant; the real floor is per-model and non-monotonic — MEDIUM

`clip_engine/scoring.py:103` — `_CACHE_FLOOR_CHARS = 4 * 1024` (i.e. 1,024 tokens).
`knowledge/util.py:37` — `_CACHE_FLOOR_TOKENS = 1024`, shared by ~10 builders.
Both are model-independent constants. Per the live docs (table above) the floor ranges 512 → 4,096
and does **not** decrease monotonically with model recency.

Two concrete consequences:

**(a) Money left on the table, today.** `score_candidates` runs on `claude-opus-5`
(`config.py:117`), whose floor is **512**, but the gate demands 1,024. `_SYSTEM_STATIC` measures
~2,690 chars ≈ 670 tokens (per the file's own comment at `:99-101`). A creator whose DNA brief is
under ~1,416 chars therefore lands in the 512–1,023 band: **above** Opus 5's real floor, **below**
this gate. No `cache_control` is emitted, and every scoring call for that creator re-pays full Opus
input price ($5/MTok) on ~670 tokens of byte-identical prefix, permanently. `config.py:114-116`
already documents *"512-token prompt-cache minimum"* for Opus 5 — the SoT knows; the code does not
consume it.

**(b) A reachable inert-marker regression.** Every model key is env-overridable by design, and
`tests/test_model_config.py` validates only the **alias shape** (`_BARE_ALIAS_RE`), not membership
in a known-good set. Setting `ANTHROPIC_MODEL_SCORING=claude-opus-4-6` — a plausible cost move,
still an active model — passes every gate. The 1,024-token gate then fires on a prefix far below
Opus 4.6's **4,096** floor, emitting `cache_control {ttl: "1h"}`. Anthropic silently declines to
cache and **returns no error**; the call pays the 2× 1-hour write premium with zero reads on every
scoring call. That is verbatim the failure Issue 315 exists to prevent
(`clip_engine/scoring.py:13-17`), reachable purely by env var.

Fix: a `CACHE_FLOOR_TOKENS: dict[str, int]` beside the existing price book in `config.py` (it is the
same kind of perishable provider constant, and the price book already has a `PRICE_BOOK_VERSION`
stamp), consumed by both gates. Note the repo has flipped this number three times
(2048 → 1024 → 2048 → 1024, snag-taxonomy Class 11) — making it model-keyed is what stops a fourth.

---

### 4. Model-swap validation covers 1 of 20 keys; no n+1 candidate lane — MEDIUM

`tests/test_scoring_goldens.py:113` (`test_golden_model_matches_configured_scoring_model`) is
genuinely excellent — changing `ANTHROPIC_MODEL_SCORING` breaks CI until the goldens are re-recorded
against the new model. It is also the **only** model-swap tripwire in the repo, covering 1 of 20
pinned keys. Nothing at all guards `ANTHROPIC_MODEL_VIDEO_CONTEXT` and
`ANTHROPIC_MODEL_CLIP_METADATA` — the other two Opus 5 calls and, with scoring, the three most
expensive in the system.

Current practice is a continuously-running n+1 lane so the migration delta is known before the
sunset clock starts. The building blocks are already here: `tests/test_llm_live_scoring.py` contains
the right probes (`test_setup_outscores_aftermath_majority_of_3`,
`test_dna_score_orders_on_brief_above_off_brief_majority_of_3` — best-of-3 ordering assertions
against the live API), and `llm-e2e-nightly.yml` already runs them nightly on GitHub-hosted runners.
The missing piece is parameterizing that lane over a `CANDIDATE_MODEL` env var so it reports the
delta rather than only the incumbent's pass/fail.

**Failure scenario:** Anthropic announces `claude-sonnet-4-6` retirement on a 60-day clock. The swap
touches 11 config keys spanning titles, thumbnails, chat, intake, analysis, DNA brief, improvement,
and three per-clip features. Because only scoring has a tripwire and none of the 11 has a golden or
an ordering probe, the only way to learn whether title quality or DNA-brief grounding regressed is
to ship it to the beta and wait for a creator to say so.

---

### 5. The quality instrument exists and its alarm has no receiver — MEDIUM

`llm-e2e-nightly.yml` runs the live behavioral scoring lane and prints one `SCORING-MARGIN` line per
ordering probe. It writes them to `$GITHUB_STEP_SUMMARY` and nowhere else
(`.github/workflows/llm-e2e-nightly.yml:123-140`). There is no notification step, no threshold, and
no persisted series — grep for `slack|notify` across `.github/workflows/` returns only the
`health-check.yml` placeholder comment. So:

- **The margins are printed but never stored.** "Is the scorer degrading?" is unanswerable across
  runs, which is exactly the thing a quality SLO is. The judge-score-distribution-over-time layer
  the 2026 guidance describes has its numerator and no accumulator.
- **A red nightly relies on GitHub's default scheduled-failure email.** This repo has already lost a
  scheduled workflow silently for six weeks (`health-check.yml`, snag-taxonomy Class 9) and carries
  a job that has failed on every merged PR since 2026-07-02 without anyone acting
  (`OFF_COURSE_BUGS.md:42`). The prior probability that this email gets acted on is low and the
  repo's own history is the evidence.

**Failure scenario:** a prompt edit to `_SYSTEM_STATIC` (not schema-affecting, so the golden
schema-hash pin does not fire — see below) moves the setup-vs-aftermath margin from comfortable to
2-of-3. The nightly still passes. Three weeks later a further change tips it to 1-of-3 and the
nightly goes red at 03:00 UTC; nothing pages, and the shipped clips have been drifting toward
aftermath windows for a month. This is the exact scenario `docs/assessment/CLIPPING_INTEGRITY_2026-08-12.md`
(Issue 476) was filed for.

Cheapest correct fix at this scale, using a pattern the repo already owns: append the
`SCORING-MARGIN` lines to a committed JSONL on each nightly run and fail the job below a **ratcheted
floor** — the identical anti-hollowing mechanism as `SCENARIO_FLOOR` in `tests/test_clip_engine.py`.
That converts a printed number into both a trend and a gate without adding an alerting dependency.

**Related, smaller:** the goldens pin `_OUTPUT_SCHEMA`'s sha256 but **not the prompt text**. Editing
the rubric inside `_SYSTEM_STATIC` — the actual definition of the scorer's judgment — leaves every
golden green, because a golden replays a recorded body. `PROMPT_VERSION` exists in exactly one place
(`analysis/video_context.py:62`, now at 3) and nowhere else; the scoring prompt has no version at
all. A prompt-text sha256 in the golden, alongside the schema hash, closes it for ~5 lines.

---

### 6. A circuit breaker is not what this system needs — over-engineered to add — LOW

*(Judgement call, argued deliberately against the audit brief's question 4.)*

Standard practice says circuit breaker + cross-model fallback. At ≤100 users on one VM with Celery
`--concurrency=2`, the layers already present are sufficient and the breaker would be net-negative:

- SDK `max_retries=2` on 16 of 17 clients (429/408/5xx/connection, exponential backoff).
- Celery `max_retries=2–3` at `default_retry_delay=60` on every LLM task, with
  `analyze_video_context` deliberately at `max_retries=0` and the reason written inline
  (`worker/tasks.py:518-526`) — a considered exception, not an oversight.
- `flags.llm_generation` DB kill switch (30s TTL cache, no deploy) plus the spend guard, both gating
  every LLM-reaching route (`routers/clips.py:401,492,641`).

A one-hour Anthropic outage therefore costs: each in-flight job burns ~2 SDK retries then 2–3 Celery
retries over ~3 minutes and fails cleanly; the queue drains rather than backs up; the owner flips
the kill switch. Adding Redis-backed breaker state would put a new dependency in the failure path of
the thing meant to protect it — the 2026 guidance's own caveat ("every reliability layer must itself
have a fallback"), and this repo already runs both the rate limiter and the spend guard **fail-open
on Redis** by recorded decision (`AUDIT_BRIEF.md` §5).

What is genuinely missing is only the **written position** plus one runbook line
(`docs/RUNBOOKS.md`): *"Anthropic degraded > 15 min → `scripts/flags.py llm_generation off`; renders
and triage continue; re-enable and the queue drains."* Cost: a paragraph. This is a case where the
audit should record "we do not need this" rather than build it.

---

## Ground-truth correction

`architecture-map.md` §C bet 11 records the 17-singleton / no-`clients.py` choice as *"⚠️ Partial —
DECISIONS 10095 covers the policy, not the no-abstraction choice. `SOT.md:112` asserts it as a rule
with no linked decision."*

**The decision does exist**, at `docs/DECISIONS.md:3940` — 2026-06-23, Issue 242, item 4: *"Module-level
singleton pattern (matching `dna/brief.py:21`) over a central `clients.py` file (which SOT.md lists
but which does not exist on disk — the actual live convention is per-module singletons)"*, with
`dna/brief.py:21` cited as evidence at `:3978`.

Two things follow, and both are worse than "no decision":

1. The rationale is **descriptive, not normative** — it records what the codebase does, not why that
   is right. It would not survive being argued against.
2. It is filed inside a **transactional-email provider** decision (Resend vs Postmark vs SES). That
   is why this audit's own ground-truth pass, grepping for LLM-client topics, did not find it. A
   decision nobody can locate is operationally equivalent to one that was never written.
3. The entry itself flags that `SOT.md` documents a `clients.py` that does not exist. Eight weeks
   later, `SOT.md:112` still says so.

**Committed answer to the brief's question 5** (is the abstraction worth it, or are the tests doing
its job?): **the tests are not doing its job, and on this evidence a thin factory wins.** The drift
predicted by architecture-map has not just happened — it has exceeded its own description. It is not
60 vs 120: it is 60, 120, 180, and one client at the SDK's 600s default, with the guard test blind
to the last of those. Seventeen call sites are not seventeen configurations; they are one
configuration copied seventeen times with one legitimate axis of variation (`timeout`, where
`video_context`'s 180s is genuinely justified by a ~26K-token input). A ~20-line
`llm/_client.py::anthropic_client(timeout_s: float) -> AsyncAnthropic` collapses that with zero
behavioral change and no new indirection — it is a constructor helper, not the `clients.py` service
registry `SOT.md` imagines, and it does not reintroduce the shared-mutable-client hazard Issue 82a
closed.

But the factory alone is insufficient and should not be built alone: **no factory can force a future
module to call it.** The AST sweep from finding 1 is the load-bearing half; the factory is the part
that makes future call sites correct by default. Build the sweep first.

---

## What is genuinely right here — specifically

- **`billing.ledger.record_llm_usage` as a single choke point, verified by a repo-wide AST sweep.**
  `tests/test_usage_coverage.py` layer 2 discovers every `messages.create`/`messages.stream` site in
  the tree and fails on any not mapped to concrete billing evidence, with per-caller markers
  (`_patterns_usage,` pins a *specific variable*, so a sibling billing line cannot satisfy it). This
  is the single best-engineered test in the LLM layer and the pattern the other two registries
  should copy.
- **Prompt caching is implemented correctly and, unusually, *observed*.** Static block first,
  per-creator DNA second, style notes appended **after** the last breakpoint with the byte-prefix
  reasoning written out (`clip_engine/scoring.py:409-427`), and the log line reads
  `usage.cache_creation.ephemeral_1h_input_tokens` to confirm the write landed in the 1-hour tier
  rather than the 5-minute one (`:456-470`). Most teams set `cache_control` and never check.
- **Refusal handling is present at all three Opus 5 sites and degrades honestly.**
  `clip_engine/scoring.py:476`, `analysis/video_context.py:409`, `knowledge/clip_metadata.py:285`
  each branch on `stop_reason == "refusal"` and fall back (keep signal scores / skip context) rather
  than indexing into an empty `content` array. Opus 5 ships elevated cybersecurity safeguards that
  return HTTP 200 with empty content, and creator-uploaded transcripts are exactly the kind of input
  that trips a false positive. This is correct and most codebases have it wrong.
- **`max_tokens` was raised for Opus 5's default-on thinking, with the reason recorded at each site**
  (1800→8000, 2000→8000, 2000→6000; `scoring.py:434`, `video_context.py:366`,
  `clip_metadata.py:244`). `max_tokens` caps thinking + text together on Opus 5; missing this is the
  most common Opus-5 migration bug and the repo caught it at all three sites.
- **`config.py:110-116` reads like it was written by someone who checked the docs**, not from
  memory: Opus 5's thinking default, sampling-param rejection, and 512-token cache minimum are all
  named, with a fetch date and the DECISIONS pointer.
- **`test_golden_model_matches_configured_scoring_model`** turns a config edit into a CI failure. It
  is the mechanism the domain's model-upgrade policy is missing — it just only exists once.
- **Prompt-injection defense.** `wrap_untrusted` (JSON-encode into an XML-labelled envelope in the
  **user** role) + `UNTRUSTED_CONTENT_POLICY` in every system prompt, single-sourced in
  `knowledge/util.py`, citing both the Anthropic mitigation guidance and OWASP LLM01:2025. Verified
  present in all 17 modules including the four missing from the conformance registry.
- **`dna_system_block` returning `None` rather than a placeholder**, with the honesty incident that
  motivated it written into the docstring (`knowledge/util.py:44-58`) — the model was being told to
  cite channel data, handed a string saying there is none, and Python then appended "grounded in
  your channel data". Keying the disclaimer off the same signal so the two cannot drift is the right
  structural fix, not a patch.

---

## Decisions this domain needs but does not have

1. **Model-upgrade policy.** Who owns the swap, what must be re-run (goldens re-recorded, live
   ordering lane re-run, cache floor re-checked), and what the acceptance bar is. Today it exists
   implicitly for scoring only, as a test.
2. **Rolling-alias behavior-drift position.** The bare-alias decision (DECISIONS ~11983) chose
   correctly on deprecation and never addressed silent re-pointing. Needs either "we accept it,
   here's the detection" (record `response.model`) or "we pin snapshots for the clip chain."
3. **Prompt-versioning policy.** `PROMPT_VERSION` exists in one module. Either every prompt carries
   one and it is stamped on the artifact + the eval row, or the position is "only video_context
   needs it, because its output is persisted" — which is defensible and unwritten.
4. **What "the scorer is performing acceptably" means, numerically.** `SCORING-MARGIN` is printed;
   no threshold, no floor, no trend. Until a number is written down, the nightly is an observation,
   not a gate.
5. **Cache-floor ownership.** The floor is a perishable provider constant that has already flipped
   three times. It needs the same treatment the price book got: a keyed table, a version stamp, and
   a named re-verification trigger.
6. **Degradation posture during a provider outage.** One paragraph plus a runbook line; explicitly
   including "we are not building a circuit breaker at beta scale, and here is why."
7. **Whether `routers/insights.py:764` keeps its own client and token accounting.** Flagged in
   `AUDIT_KNOWN_ISSUES.md` §G as "the one non-uniform call site"; still unresolved, and it is one of
   the four modules the conformance registry misses.
