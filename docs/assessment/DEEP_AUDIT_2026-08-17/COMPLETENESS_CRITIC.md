# Completeness critic — what this audit missed

**Produced:** 2026-08-17, Phase 3. **Posture:** adversarial toward the audit, not the codebase.
**Method:** read the ground-truth pack, all 12 domain reports, all 6 sweep reports and both findings
JSON files; then ran my own read-only measurements against the repo with `.venv/bin/python`. Every
number below that is labelled **[measured]** I produced myself in this session and the command shape
is given. Nothing here was taken on trust from the finding text.

**Headline:** the audit's *findings* are reliable; the audit's *coverage* is not uniform; and its
*severity ratings* are the least trustworthy thing in it — every one of the 21 findings that survived
adversarial verification was downgraded, none upgraded (§6). Three of the six modalities did their job
well. The single biggest omission is not a missed defect — it is that **67% of the frontend and the
entire third-party-integration edge (`ingestion/transcribe.py`, `upload_intel/`, `improvement/`,
four `knowledge/clip_*` modules) were never opened**, and those are precisely where the two most
production-escaping failure classes live (Class 3 SDK surprise, Class 5 honesty inversion).

---

## 1. What was never looked at

I measured attention by counting every occurrence of each repo path across all 21 audit artifacts
(3 ground-truth + 12 domain + 6 sweep) plus the three JSON files — 1.17 M characters of corpus.

### 1a. Backend, by module **[measured]**

| module | LOC | mentions | reports touching it |
|---|---:|---:|---:|
| routers | 10,733 | 97 | 14 |
| worker | 8,692 | 126 | 14 |
| clip_engine | 8,365 | 53 | 15 |
| *(root .py)* | 5,988 | 331 | 20 |
| scripts | 4,058 | 84 | 14 |
| **knowledge** | **3,021** | **15** | **7** |
| youtube | 2,084 | 12 | 7 |
| preference | 1,527 | 19 | 7 |
| dna | 1,520 | 13 | 6 |
| billing | 1,269 | 37 | 9 |
| **chat** | **1,193** | **9** | **5** |
| **ingestion** | **1,002** | **6** | **3** |
| analysis | 647 | 6 | 4 |
| notify | 449 | 7 | 2 |
| **improvement** | **241** | **1** | **1** |
| **upload_intel** | **98** | **0** | **0** |

107 of 213 production `.py` files are never named anywhere in the corpus (59 of those are
migrations — see 1d).

### 1b. The five specific holes that matter

**1. `ingestion/transcribe.py` — 408 LOC, ZERO mentions, in ZERO reports.** This is the highest-value
omission in the audit. It is the Deepgram / AssemblyAI / WhisperX backend switch, i.e. the live
third-party surface for the *default* transcription path. Class 3 (third-party SDK/API surprise) is
the taxonomy's "single most *expensive* class" and 6 of 8 `ISSUES_LOG` entries. **This file's own
comments document two Class-3 incidents that already happened inside it** — `PrerecordedOptions`
rejecting a param and raising `TypeError` "on every prod upload" (`:141-148`), and `mip_opt_out` not
being accepted as a constructor kwarg (`:172-176`, deepgram-python-sdk issue #474). The audit
identified Class 3 as the most expensive class and then never opened the file where Class 3 has bitten
twice. **[measured: `grep -c` over the corpus = 0; comments read directly.]**

**2. Four `knowledge/clip_*` LLM feature modules — 1,106 LOC, ZERO mentions:** `clip_titles.py` (307),
`clip_explain.py` (306), `clip_captions.py` (252), `chapters.py` (241). Plus `analysis/brief.py` (229)
and `upload_intel/timing.py` (98), also zero. All are creator-facing LLM output paths, i.e. Class 5
(honesty inversion) territory. d04 covered the LLM *layer* (clients, caching, retries); nobody
audited what these six actually say to a creator.

**3. Five routers never named: `publications.py` (348), `analysis.py` (303), `api_keys.py` (228),
`improvement.py` (198), `titles.py` (101), `upload_intel.py` (56)** — 1,234 LOC of HTTP surface.
`api_keys.py` is an authentication-credential surface and d07 (security & tenancy) never opened it.
*Honest note:* I spot-checked it and found nothing wrong — SHA-256 over a high-entropy random key is
the correct standard, and it carries `10/hour` + `60/minute` creator-keyed limits. Absence of audit
attention here is not evidence of a defect; it is absence of evidence.

**4. The frontend is two-thirds unexamined. [measured]** 165 non-test source files, 21,545 LOC.
**129 files / 14,383 LOC (67%) are never named by path or basename in any report.** Never-named files
include `ShortFormEditor.tsx` (601), `video-player.tsx` (571), `Review.tsx` (461), `VideoTable.tsx`
(441), `ClipMetadataPanel.tsx` (385), `Chat.tsx` (348), `Onboarding.tsx` (318). d06 is a good report
about the *contract* (SPA↔API, a11y tooling, coverage measurement) and is not a review of the
product surface. Given that Class 5 (~15 rows, "the app tells the creator the opposite of the truth")
escapes to production every time and is a frontend class — `YourCall.tsx` rendering an error in
success green, `LongFormEditor.tsx` drawing a fabricated waveform — this is the audit's largest
uncovered blast radius.

**5. `alembic/versions/` — 59 of 62 migration files never named.** d02 audited the *data model*;
nobody audited the *migrations as artifacts*, which is where the "every prod migration was a silent
no-op" incident and the invalid-PostgreSQL `MIGRATIONS.md` Rule 4 snippet both live. `alembic/env.py`
got 4 mentions in 3 reports; the versions directory got effectively none.

### 1c. Everything else with zero attention

- **Workflow:** `.github/workflows/docker-publish.yml` (106 lines) — 0 mentions. It builds and pushes
  the image the deploy job then pins; d11 covered ci/deploy and skipped the publish link in the chain.
- **Scripts:** `repro_ingest_render.py` (131), `repro_render.py` (124), `clip_pipeline_state.py` (125),
  `setup-runner.sh` (109), `setup_hooks.sh` (10) — 0 mentions each. `setup_hooks.sh` is notable: the
  ground truth says the pre-push hook "was not installed on this clone," and nobody read the installer.
- **`tests/perf/`:** exists (`locustfile.py`, `seed_staging.py`, `README.md`). `seed_staging.py` is
  used by the staging drills and deploy; **no workflow or script invokes locust at all** — the load
  scaffold `docs/SOT.md` advertises as "concurrency evidence" is never run. Unexamined by the audit.
  *(Lead, not a finding — I verified only the absence of an invocation.)*
- **`Dockerfile`** got 6 mentions in 4 reports — thin but not absent; `requirements.txt` got 2.

### 1d. What is genuinely well-covered

For fairness: `main.py`/`config.py`/`db.py`/`models.py` (the root layer) drew 331 mentions across 20
of 21 reports; `docker-compose.prod.yml` 34 across 13; `scripts/doctor.py` 30 across 9;
`worker/tasks.py` 126 across 14. The deploy/gate/probe spine is thoroughly worked. The audit is deep
where the incidents were and shallow where they weren't — which is defensible, but it means the report
must not be read as a statement about the *whole* system.

---

## 2. Claims still unverified that would most change the conclusions

35 phase-1 findings are `NOT_CONTESTED`; 34 phase-2 medium/low candidates are unverified. Ranked by
how much the report changes if the claim is wrong, with the one check that settles each.

| # | Claim | Why it is load-bearing | Single settling check |
|---|---|---|---|
| 1 | **d07 `rls-coverage-not-derived`** — `creator_identity` and `creator_api_keys` have a `creator_id`, no RLS policy, and no recorded exemption; the tenant-table list is a hardcoded tuple in a test file | If true this is a live tenancy gap on the API-credential table and the report's security posture flips. Class 7 (five SEV1/2s on one fault line) says RLS gaps "only show up against the live role" | On the integration lane as the **non-privileged app role**: `SELECT tablename FROM pg_policies` vs. `SELECT ... FROM information_schema.columns WHERE column_name='creator_id'`; diff the two sets. Not greppable — must run against real Postgres |
| 2 | **mC-5** — `live_smoke.check_pipeline` reads back the rows `live_smoke._seed()` just wrote and prints four `pipeline: … PASS` lines | `live_smoke.py` is the only probe that touches *live production*. If it is circular, the project's single live-verification instrument proves nothing, and several "verified in prod" claims elsewhere inherit that | Read `_seed()` and `check_pipeline()` side by side and confirm whether any asserted row is produced by the *pipeline* rather than by `_seed`. ~15 minutes, no prod access needed |
| 3 | **mC-6** — no probe anywhere performs an R2 **write**; three checks attest "storage healthy" from read-only ops | This interacts with a *verified* finding and weakens it. E1's correction rests on "R2 IS live-gated on every deploy" via `_check_storage() → head_bucket`. `head_bucket` is a read. If mC-6 holds, the deploy's one real provider gate proves bucket existence + credential validity, **not** write capability — and "uploads silently FAILED" (`ISSUES_LOG:542`) is a write failure. The verifiers did not notice the interaction | `grep -rn "put_object\|upload_file\|upload_fileobj" scripts/ main.py` — if zero, mC-6 stands and E1's correction needs softening |
| 4 | **d09 `no-refund-or-dispute-handling`** — refunds/chargebacks from Stripe are unhandled; reconciliation runs Stripe→ledger only | Money path, and Stripe is the subsystem with a 10-week outage and a four-layer defect history. An unhandled `charge.refunded` means minutes stay granted after a reversal | `grep -n "charge.refunded\|charge.dispute\|payment_intent.canceled" routers/billing.py` and check the webhook event allowlist |
| 5 | **d03 `idempotency-guards-persist-not-spend`** — the idempotency pattern guards persistence but not the paid call; the loser of the generate-clips race has already bought an Opus-5 scoring call | Money leak proportional to concurrency, and it contradicts the "idempotent + retry-safe" line in the CLAUDE.md Phase-4 checklist | Read `generate_clips`'s advisory-lock/early-return ordering vs. the `score_candidates` call site; confirm whether the lock is taken before or after the LLM call |
| 6 | **A6** — the RLS activation workflow's only verification passes vacuously against an empty `videos` table | It is the gate over the security control from the Class-7 cluster; a vacuous gate there is the exact `:25` pattern repeating a third time | Read `.github/workflows/activate-rls.yml`'s verification step; check whether it asserts a non-zero row count before asserting isolation |
| 7 | **F5** — clickwrap consent versions are recorded at signup and never compared, so a policy bump re-prompts nobody | Google OAuth app verification (Issue 29) is an open external gate and the Privacy Policy is load-bearing for it. F1's verification already found one published-policy misstatement | `grep -rn "consent_version\|POLICY_VERSION" routers/ main.py auth.py` — is the stored version ever compared to a current constant? |
| 8 | **d02 `no-alembic-check-index-drift`** — 20 of 28 production indexes are absent from `Base.metadata`, no CI job compares models to schema | Determines whether the migration surface can silently drift, which is the "prod DB 7 revisions behind" incident's family | On the integration lane after `alembic upgrade head`: `alembic revision --autogenerate --sql` and check whether it emits any DDL. Non-empty output = drift confirmed |

Two more worth naming without a full row: **d10 `required-set-unpinned`** (nothing machine-checks
which checks are required; `Visual regression` documents itself as gating and is not) — settled by
`gh api repos/:owner/:repo/branches/main/protection` vs. the workflow names; and **d08
`no-docker-log-rotation`** — settled by `grep -n "max-size\|logging:" docker-compose.prod.yml`. Both
are cheap and both are single-VM outage paths.

---

## 3. False positives — I tried to kill five phase-2 findings

Phase 2 corrected 12 of 12 and refuted 0. I picked five I judged weakest and attacked them. **Four
survived intact, one survived with a false repro.** The self-filtering explanation is the better one:
the sweepers' brief required a repro, and that filtered most junk before verification. But the
verifiers *were* lenient about details — see mC-7.

| id | verdict after my attack |
|---|---|
| **F7** (thumbnail-patterns has no caller) | **SURVIVES.** `grep -rn "thumbnail-patterns\|thumbnail_patterns"` over `frontend/src` and `static/` → **0 hits**. Python hits are the route definition, its own lock key, and two comments. The endpoint is genuinely unreachable from the product. |
| **F8** (`demographics` read by nothing) | **SURVIVES, and is stronger than filed.** `grep -rnw Demographics` over all production packages: writes in `youtube/analytics.py:426-431`, one `delete()` in `worker/tasks.py:4399`, nothing else. **New, verified by me:** `worker/tasks.py:5030` is a code comment asserting that empty `AudienceActivity`/`Demographics` "silently disabled `optimal_upload_gap_h` in the DNA build, upload_intel's `data_available`, and the chat audience tool" — I checked all three and **every one reads `AudienceActivity` only** (`dna/builder.py:321`, `routers/upload_intel.py:45`, `chat/tools.py:348`). The comment attributes three consumers to a table that has none. **Correction to the candidate:** the payload is `viewerPercentage` by `ageGroup,gender` — channel-level aggregate percentages, not PII. "PII-adjacent" overstates it; the data-minimisation argument (a scope justified to Google for a purpose that does not exist) stands on its own. |
| **F6** (`/billing/packs` dead; price duplicated in TS) | **SURVIVES, severity over-rated.** Confirmed: only frontend references are the two "keep in sync" comments, no fetch; `Pricing.tsx:21-29` retypes six packs. **Correction the verifier would have made:** the server is the price authority — the client sends `pack_id` only (`routers/billing.py:140`), Stripe is charged from `PURCHASABLE_PACKS[pack_id].price_cents` (`:313`), and Stripe Checkout displays the true amount before the creator confirms. So the failure is a **pre-checkout misquote on the marketing page**, not a wrong charge. Real (it is Class 5 honesty-inversion shaped) but low-to-medium, not medium. |
| **E11** (`STRIPE_WEBHOOK_SECRET` absent from deploy secret sync) | **SURVIVES, with corroboration the finder missed.** `grep -n STRIPE .github/workflows/deploy.yml` → exactly two lines, both `STRIPE_SECRET_KEY`; 13 other secrets are synced. **Corroborating:** `docs/DECISIONS.md:124-125` describes the rejected alternative as requiring rotation of "`STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` on the VM **and in GitHub Secrets**" — the project believes both live in GitHub Secrets; the sync pushes one. **Correction:** the app cannot boot in prod without the value (`config.py:1135-1141` `_require_prod_secrets`), so it is not *missing* — it is *not rotatable through the documented path*, which is narrower and correctly LOW. |
| **mC-7** (ffmpeg-routing test is a literal-vs-literal tautology) | **Core SURVIVES; its repro output is FALSE.** The test body is `expected = {five literals}; assert set(RENDER_TASKS) == expected` — confirmed by reading `tests/test_celery_routing.py:29-38`. But I ran the candidate's own repro script verbatim and it printed **two** unrouted tasks (`backfill_video_peaks`, `backfill_video_camera_regions`), **not the three claimed** — `ingest_video` never appears, because it reaches ffmpeg through imported helpers (`youtube.ingest.extract_audio_wav`, `ingestion.audio.generate_waveform_image`) that the script's regex over the task body cannot see. The substantive claim (unrouted ffmpeg work on the default queue) holds at n=2. |

**What this tells you about the ratio.** 0/12 refuted is not evidence of verifier failure — I could
not refute any of my five either. But mC-7 shows a shipped `repro` block whose *stated output does
not match what the script produces*, and it passed to shipping unchallenged. **Treat `has_repro:
true` as "a repro exists," not "the repro was run and matched."** For the 21 findings that went
through adversarial verification, the verifier's own `repro_detail` (which quotes real terminal
output) is trustworthy; for the 34 unverified candidates, the repro blocks are claims.

---

## 4. False negatives — the weakest modality, and one I ran myself

### 4a. Modality B's "near-clean" verdict is credible — I checked **[measured]**

The brief asked whether "always-true assertion" and "empty iteration" coming back near-clean across
3,068 test functions is believable. I ran an independent AST sweep over `tests/**/test_*.py`:

```
total test functions scanned: 3103
ZERO assert AND ZERO assertion-shaped call: 33  (1.1%)
```

I then read all 33. **None is a vacuous passing test.** Six raise `NotImplementedError` (they fail
loudly if ever collected), several assert inside a nested `_run()` closure my AST did not attribute
to the outer function (`test_llm_live_scoring.py:224` is this shape), and the rest use
`pytest.raises`-adjacent constructs. **Modality B was right.** That is a real, positive result and
the report should say so rather than leaving it hedged.

### 4b. But modality B missed the adjacent class it should own: skipped tests **[measured]**

```
unconditionally-skipped test functions (mark.skip, not skipif): 62
```

- **54 are Issue-226 gravestones** — tests for `static/index.html`, `profile.html`, `review.html`,
  `insights.html`, `analysis.html`, `onboarding.html`, `pricing.html`, `walkthrough.html`, all
  deleted. They are inert markers inflating the test count. Several are named
  `test_index_escapes_third_party_video_title`, `test_insights_escapes_llm_and_persisted_content`,
  `test_analysis_escape_includes_apostrophe_via_shared_util` — **the XSS-escaping suite for
  creator-facing LLM output is 100% skipped**, with the skip reason "XSS risk eliminated structurally
  (Issue 226)." That claim is plausible (React escapes by default) but it is asserted in a skip
  reason, tested nowhere, and the React surface that replaced those pages is the 67% of the frontend
  nobody audited. This is a *lead*, not a finding.
- **8 are live debt.** `TestNotificationsIntegration` (3) and `TestNotificationsTriggersIntegration`
  (3) are skipped with `reason="staging-pending: needs real Postgres + RLS (Issue 275)"` and their
  bodies `raise NotImplementedError("staging-pending")`. What they would verify: the UNIQUE
  `dedupe_key` constraint, **RLS blocking cross-creator notification reads**, and exactly-once
  delivery on Celery retry. **Issue 275 is the GKE-staging linchpin of Lane L12 — descoped for v1 per
  `DECISIONS.md` 2026-06-26.** So three tenancy/idempotency guarantees are parked behind a lane that
  will not be built. And the dependency label is wrong: the repo already has a real-Postgres
  integration lane (`-m integration`, docker-compose PG16+pgvector) that could run all six today. No
  GKE cluster is required for "needs real Postgres + RLS."

### 4c. The missing seventh modality, and its result **[measured]**

All six modalities ask *"is this signal lying?"* None asks **"does this citation still resolve?"** —
even though the ground truth names that exact failure (`OFF_COURSE_BUGS:159`: *"docs cite code by
path/line and nothing verifies the citation still resolves"*), and even though that failure **seeded
Issue 497**, the bandit/mypy hole, via a stale `CLAUDE.md` structure list. It is the cheapest possible
modality and nobody ran it. I did.

**Modality G — citation resolver.** Extract every repo-rooted `path[:line]` from the 17 governing
docs (`CLAUDE.md`, `SOT.md`, `DECISIONS.md`, `COMPLIANCE.md`, `GO_LIVE.md`, `RUNBOOKS.md`,
`PROJECT_STATE.md`, `issues.md`, `OFF_COURSE_BUGS.md`, …); assert the file exists and the line is
within EOF. URLs stripped, `.tsx` matched before `.ts`, only paths rooted at a real top-level package.

```
repo-rooted path citations scanned : 1670
  MISSING FILE                     :  122  (7.3%)
  LINE NUMBER PAST EOF             :    6
```

Most of the 122 are historical `static/*.html` references inside dated `DECISIONS.md` entries, which
is fine — those are history. **The four that are not fine:**

1. **`docs/SOT.md:100` lists `.github/workflows/quality.yml` in the canonical project-structure tree
   as "ratcheted CI gates (types/coverage/SAST/CVEs)". That file does not exist.** The gates live in
   `ci.yml:251,434` (`run_layer0.py`). SOT is *the* architecture source of truth, not history — and
   this is precisely the shape that seeded Issue 497. **The same defect class, still live, in the
   document the CLAUDE.md read-order puts first.**
2. **`docs/issues.md` — the active work queue — carries 6 line citations into
   `frontend/src/pages/Editor.tsx` at lines 296, 418, 438, 476-487, 532, 609, 650-654. The file is
   206 lines.** Any session that picks up those issues starts by re-deriving what the issue meant.
   That is the "baby snag" mechanism operating inside the tracker itself.
3. `docs/issues.md` and `docs/OFF_COURSE_BUGS.md` both cite
   `frontend/src/components/review/WhyThisClip.tsx`, which no longer exists (a *different* issue in
   the same file, line 296, instructs deleting it — the queue contradicts itself).
4. `docs/SOT.md:156-158` lists four `static/*.css` files that were deleted with Issue 226.

**Cost to run: ~40 lines of Python, sub-second.** This is the mechanism the project has been
prescribing to itself in prose for months. It should be a CI check.

### 4d. Modality G-2, run and reported NEGATIVE

I also ran "which production module is named by no test?" It returned 6 candidates, and on inspection
**all six are false positives** — routers are exercised over HTTP, not by dotted import
(`/creators/me/api-keys` appears 35× in tests; `/videos/{id}/feedback` is covered by
`tests/test_video_feedback.py` and its integration twin). **Honest result: no production module in
this repo is entirely untested.** Reporting the negative because it is load-bearing — it means the
audit's blind spots in §1 are *audit* blind spots, not *coverage* blind spots, and the owner should
not read "never examined" as "unprotected."

---

## 5. Is the central diagnosis right?

**The diagnosis:** "the diagnostic half is excellent, the corrective half is missing; prose instead of
mechanism." I think it is **right about the shape and wrong about the size** — and if the 90-day plan
is sized as if mechanisms address the whole problem, it will aim ~60% of its effort at the wrong
target. The strongest case against, in three parts.

### Alternative A — most of the pain is unpreventable by any internal mechanism

The ground truth names Class 3 (third-party SDK/API/platform surprise) "the single most *expensive*
class," ~20 rows plus 6 of 8 `ISSUES_LOG` entries, and its own escape column reads **"Production —
always."** Every instance is *a documented vendor default that differs from the assumed default*:
Google's `fields=` projection dropping `kind`; `stripe.HTTPXClient`'s empty CA trust store; Cloudflare
OWASP blocking Stripe's IPs; pytest `-m` replacing rather than intersecting `addopts`; alembic's
auto-begin swallowing every migration; boto3 defaulting to SigV2. **No gate, no lint, no structural
test, no registry can catch any of these.** They are only discoverable by executing the real call
against the real vendor. The corrective is not a mechanism, it is a *habit*: one real transaction per
integration per week. The taxonomy already says this — incident #1's earlier-catch is "**a single real
$1 purchase in the first week**"; incident #3's is "**one real end-to-end upload, once**." A plan built
around mechanisms will build gates for Class 1 and leave the most expensive class untouched.

### Alternative B — the diagnosis's own #1 class is, by its own words, mechanism-proof

The taxonomy's escape table says Class 1 (vacuous green, 26 rows, "the house style of failure") is
caught by **"Deliberate audit only (by construction — no gate can catch a gate)."** So the audit
identifies a class as #1, states that mechanisms cannot catch it, and then prescribes mechanisms. The
corrective that has *demonstrably* worked in this project is the periodic adversarial audit: 27 issues
on 2026-08-12, 8 SEV1/2s on 07-29, and this audit's 141 findings. The honest prescription may be
"**schedule the audit quarterly and fund it**," which is a calendar entry, not an engineering program.

### Alternative C — the sensation may be backlog visibility, not defect arrival rate **[measured]**

I measured the commit-type mix by month:

| month | commits | feat | fix | fix/(fix+feat) |
|---|---:|---:|---:|---:|
| 2026-05 | 177 | 23 | 42 | **65%** |
| 2026-06 | 299 | 92 | 63 | **41%** |
| 2026-07 | 307 | 43 | 91 | **68%** |
| 2026-08 (to 15th) | 184 | 58 | 32 | **36%** |

**August is the lowest fix-ratio in the project's history.** The defect arrival rate is not rising.
Meanwhile 52 of 138 `OFF_COURSE_BUGS` rows are Open and only 10 were ever promoted to `issues.md` —
the log "has no closing pressure." The feeling of "one baby snag after another" is fully consistent
with **a growing open list plus 27-issue audit bursts**, not with a deteriorating codebase. If that is
the cause, the fix is a weekly triage that empties the log — no new mechanism required.

**Caveat, stated honestly:** August is 15 days at the project's *highest* daily commit rate (12.3/day
vs. July's 9.9), and the 08-12 audit's 27 issues were explicitly deferred (`issues.md:4457`, "deliberately
NOT built, per the owner's scope call"). August's low fix ratio may be deferral rather than health,
which would *support* the backlog explanation rather than undercut it.

### What would distinguish them

**Repeat rate is the discriminator, and the audit did not compute it.** Mechanisms prevent
*recurrence*. The taxonomy documents roughly 5–8 explicit repeats across 138 rows (~5%): the vitest
flake three times, the Node-version gotcha twice, a structural gate false-positiving on a comment "for
the second time," the RLS test vacuity. **~95% of logged snags are first instances of novel problems**,
which mechanisms cannot prevent — only detect sooner.

But that number understates the diagnosis, because it counts *instance* recurrence and the audit is
claiming *shape* recurrence — 26 rows sharing the vacuous-green shape is real, and shape recurrence
**is** mechanism-addressable. Sizing it by class:

- **Mechanism-addressable:** Class 1 (26) + Class 4 config drift (18) + Class 6 doc drift (12) ≈ **56 of 138 ≈ 41%**
- **Habit/exercise-addressable, not mechanism:** Class 3 (20) + Class 10 clip-engine domain (27 issues) + much of Class 2 test-infra (35)

**Verdict on the diagnosis: keep it, but bound it.** "Prose instead of mechanism" is a correct
description of ~40% of this project's pain, and the 90-day plan should say so in its first paragraph.
The other ~60% needs a different instrument — a weekly real-money/real-upload exercise against every
live integration, plus a funded recurring audit — and if the plan does not carry those two items with
equal weight, it will make the fix-ratio look better without changing what the owner actually feels.

---

## 6. Verdict on the audit's reliability

### Trust as stated

- **The ground-truth pack** (`snag-taxonomy`, `process-map`, `architecture-map`). It is sourced to
  commits, `OFF_COURSE_BUGS` rows and `ISSUES_LOG` entries. Every number I spot-checked reproduced.
- **The 8 CONFIRMED and 36 CORRECTED phase-1 findings**, read via the corrected statement.
- **The 21 adversarially verified phase-2 findings** (12 + the 9 highs), read via the corrected
  statement, and specifically their `repro_detail` blocks, which quote real terminal output.
- **Modality B's "near-clean" verdict on always-true assertions.** I re-ran it independently and it
  holds at 33/3103, all benign.
- **The domain verdicts on the deploy/gate/probe spine** (d08, d11, mA, mC). That surface drew the
  most attention from the most reports and the findings interlock.

### Treat as leads requiring your own confirmation

- **All 35 phase-1 `NOT_CONTESTED` findings** and **all 34 unverified phase-2 medium/low candidates.**
  Nothing has attacked them. The eight in §2 are the ones worth settling first.
- **Every severity rating that did not go through verification.** This is the audit's weakest
  dimension by a wide margin. **[measured]** Across the phase-2 corpus, of the **21 findings that were
  adversarially verified, 21 were downgraded and 0 were upgraded** — the first batch of 12 all went
  high→medium (A1, A3, A4, B1–B4, mC-1/2/3, D-1, D-3), and the 9 highs verified in this workflow went
  high→medium (D-2, D-4, E1, E2, E3, F1) or high→low (E7, F2, F3). Meanwhile all 34 *unverified*
  candidates still carry their originally-filed severity. **The unverified `severity` field is
  systematically hot by roughly one full level.** Re-rate before you schedule anything.
- **Every `repro` block on an unverified candidate.** mC-7's shipped repro output does not match what
  its own script prints. `has_repro: true` means a repro was written, not that it was run and matched.
- **Any implied claim about the frontend**, `ingestion/transcribe.py`, `upload_intel/`,
  `improvement/`, `analysis/brief.py`, the four `knowledge/clip_*` modules, or
  `alembic/versions/*`. These drew ≤1 mention each. **Silence there is not a clean bill of health** —
  it is the audit not having looked, in exactly the two classes (Class 3 SDK surprise, Class 5 honesty
  inversion) that escape to production most reliably.

### The three things I would add to the report before it ships

1. **A coverage statement.** One paragraph saying what fraction of the system was examined (backend
   spine: deep; frontend: 33%; integration edge: ~0%) so the owner does not read absence of findings
   as absence of defects.
2. **Modality G as a CI check**, not a finding. ~40 lines, sub-second, and it closes a class the
   project has been describing to itself in prose since `OFF_COURSE_BUGS:159`. Start by fixing
   `docs/SOT.md:100` (`quality.yml` does not exist) and the 7 dead citations inside `docs/issues.md`.
3. **A sizing sentence on the diagnosis** — "prose instead of mechanism" explains ~40% of the corpus;
   name the instrument for the other 60% (weekly real-transaction exercise per live integration;
   recurring funded audit) with equal billing, or the plan will optimise the measurable half.
