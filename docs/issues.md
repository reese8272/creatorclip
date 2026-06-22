# CreatorClip — Issue Backlog

> **Rebuilt 2026-06-22.** The complete historical record of finished work (Issues 1–165 +
> the 166–180 research initiative) is archived verbatim in
> `docs/archive/issues_snapshot_2026-06-22.md`. This file is the **live, forward-looking
> backlog only**: carry-over open work + the implementation issues harvested from the
> gap-closure research findings (`docs/research/findings/`).
>
> **Traceability:** every research-derived issue carries a `Src:` pointer to the finding +
> its original sub-ID. The finding holds the full acceptance criteria, `file_path:line`
> evidence, and draft `docs/DECISIONS.md` entries — this backlog is the condensed tracking
> surface. `[DEC]` flags an issue that needs a `docs/DECISIONS.md` entry before/at build.
>
> **Priority order** (per the 2026-06-22 close-out): **Functionality → UI → UX →
> Agentic/Caching/Cost → Security → Observability → Notifications → Privacy/Compliance →
> DR/Infra/Scale → QA/Release-eng**, then the carry-over open items, then the deferred
> parking lot.
>
> **Scope decisions baked in (2026-06-22):** Stream-VOD recap = **expand v1 now** (§P1).
> Publishing = **export + YouTube publish in scope** (§P1); TikTok/Reels deferred (§Parking).
> Multilingual = **English-only v1**; the entire i18n track is deferred (§Parking).
> Editor = **full timeline tool** (§P1). These four require DECISIONS entries at build.

---

# Carry-over open work

## Open off-course bugs (promoted, still open)

| # | Bug | Severity | Promoted into |
|---|-----|----------|---------------|
| OCB-1 | TestClient `StarletteDeprecationWarning` (httpx → httpx2) | cleanup | Issue 274 (QA cluster) |
| OCB-2 | Dashboard clip-counts N+1 (one `GET /videos/{id}/clips` per done video) | SEV3 | Issue 213 (batched counts endpoint) |
| OCB-3 | Live video-analysis + title-optimizer flows time out at 60s | SEV3 | Issue 274 (QA cluster) + investigate latency |

Full resolved history: `docs/archive/off_course_bugs_snapshot_2026-06-22.md`.

## Open pre-existing issues (original numbers retained)

These pre-date the research initiative and remain open. Several are now **superseded or
overlapped** by research-derived issues — flagged inline.

- **Issue 24** — Production environment configuration (`.env` secrets, `ALLOWED_ORIGINS`, GH Actions secrets). BETA deploy gate. 🔲
- **Issue 25** — External API services provisioning (Anthropic, Voyage, Deepgram, R2). BETA deploy gate. 🔲
- **Issue 26** — Google OAuth consent screen + beta test users. BETA deploy gate. 🔲
- **Issue 27** — YouTube API quota check + backoff verification. BETA gate. 🔲 — *overlaps Issue 260 (quota at scale).*
- **Issue 28** — Beta go-live smoke test + friend onboarding. BETA gate. 🔲
- **Issue 29** — Google OAuth app verification (external Google review). PROD gate. 🔲 — *now also gated by Issue 194 (`youtube.upload` audit).*
- **Issue 30** — Production hardening + public go-live (load test, all gates green, v1.0.0). PROD gate. 🔲
- **Issue 58** — psycopg3 prepared-statements / PgBouncer + pool math. Code complete; **staging Locust verification pending** → closed by Issue 261. ◐
- **Issue 73** — Pydantic `response_model` + input validation coverage. Security item done; response-model coverage long-tail. ◐
- **Issue 75** — SEV-2 / cleanup long tail + dependency CVEs + compliance (tracking). Open. 🔲
- **Issue 76** — Post-hardening `/assess` re-run findings (tracking). Open. 🔲
- **Issue 78** — Salvage net-new work from closed PR #6 (re-implement cleanly on `main`). Open. 🔲
- **Issue 82** — Issue-38 Wave 2: AsyncAnthropic + AsyncVoyage migration + router session-order refactor. 🔲
- **Issue 93** — Insights page rebuild ("what is it even showing?"). 🔲 — *tracked here as the canonical Insights-rebuild; see also Issue 213 (clips map) for overlap.*
- **Issue 94** — Clip-engine transparency (show what's clipped, why, what's not). 🔲 — *largely delivered by Issue 213 (per-video clips map); keep open for the "what's NOT clipped" half.*
- **Issue 96** — Multi-step chat-driven intake form (supersedes Issue 83). 🔲 — *interacts with Issue 204 (identity-gate resolution); sequence 204 first.*
- **Issue 99** — UI redesign (Linear-style base + monospace register). Phase 1 done; **mostly superseded** by the Issue-85 React overhaul. ◐ — *keep only the un-delivered "monospace data register" polish; otherwise close.*
- **Issue 100** — Onboarding tutorial / "what this app does" gate. 🔲 — *overlapped by Issues 204/214/215 (onboarding flow rework); fold in.*
- **Issue 109** — Deferred design-work cleanups (Wave-9 follow-up). 🔲
- **Issue 132** — YouTube Live Chat spike detection. ⛔ Blocked on API availability (see DECISIONS).
- **Issue 148** — UI design-system migration: deep CSS dedup. ◐ Partial (visible cohesion done; dedup deferred).
- **Issue 150** — OBS live-feed capture (continuous program feed, ToS-clean source; extends Issue 95). ☐ Planned.
- **Issue 151** — Beta logging to a dedicated logs database. ◐ In progress — *coordinate with Issues 233–241 (observability) so the logs DB is the queryable sink.*
- **Issue 160** — Cross-page active-tasks panel (single-owner SSE store). ☐ **Superseded by Issue 211** (global active-tasks panel). Close on 211.
- **Issue 161** — Backend `next_action` envelope URLs still point at dead `/static/*` pages. ☐ — *fold into Issue 235's resolver cleanup (07/193).*

---

# New backlog — gap-closure research initiative

> Issues 166–180 (the research passes) are **complete**: each brief is produced and its
> implementation sub-issues are filed below. Mark 166–180 done in `PROJECT_STATE.md`.

## Priority 1 — Functionality

### Issue 181: Loudness normalization on every render ✅ DONE (2026-06-22)
**What:** Add `-af loudnorm=I=-14:TP=-1.5:LRA=11` to `render_clip_file` + `render_cleaned_clip_file`; wire or drop the dead `pyloudnorm` pin; correct `docs/SOT.md:19`.
**AC:** clips measure −14 ±1 LUFS; no pumping on a quiet→loud test; `pyloudnorm` used-or-removed; cites Principle 5. **Src:** 03 / A1.
**Shipped:** Two-pass `loudnorm` (measure→apply `measured_*` with `linear=true`) — no pumping; near-silent (`≤−50 LUFS`) guard skips normalization; graceful flat-render fallback on measurement failure. `pyloudnorm` pin removed; `SOT.md:19` corrected; Principle 5 cited in `render.py` docstring. `docs/DECISIONS.md` 2026-06-22 (single→two-pass deviation). Tests: `tests/test_render.py` (parse/measure/skip-silent/apply). ⚠️ The −14 ±1 LUFS *empirical* `ebur128` check is verified-by-construction in unit tests; the binary measurement must run in the render env (ffmpeg CLI absent in this dev box).

### Issue 182: Export presets — 1:1 + 16:9 renders + clip download endpoint
**What:** Parameterize `render.py` W/H for a `square` (1080×1080) + `16:9` preset; add `GET /api/clips/{id}/download` (presigned R2 / attachment) + a Download button in `ClipPlayer.tsx`. Single preset registry shared with the editorial work (no duplicate).
**AC:** creator picks preset → render produces variant → download serves it; 9:16 byte-identical when unchanged; cross-creator download → 404. **Src:** 03 / A3 + 13 / D0a + D0b.

### Issue 183: Keyword / emoji highlight in captions ✅ DONE (2026-06-22)
**What:** Extend `captions.py` so selected tokens render in a highlight color (`\c` override, mechanism proven at `captions.py:213`). v1 keyword set from transcript salience.
**AC:** ≥1 new style emits ≥1 colored keyword/phrase; plain fallback when none; `VALID_STYLES` + `CaptionStylePanel.tsx` + eval/unit test updated. **Src:** 03 / A2.
**Shipped:** New `bold_pop_highlight` style — punch-yellow (`#ffd400`) `\c` highlight on the top salient token per phrase via a dependency-free per-phrase scorer (stopwords + clip TF + casing + length); plain-Bold-Pop fallback when no salient token. Added to `VALID_STYLES` + `CaptionStylePanel.tsx` dropdown + 3 unit tests; existing styles byte-identical (regression test). DRY fix: worker transcript-load gate now keys off `captions.VALID_STYLES`. `docs/DECISIONS.md` 2026-06-22 (pure-Python over YAKE). Keyword text content (vs the `\c` mechanism) is verified in unit tests; visual review pending the render env. *(Emoji insertion + DNA-driven selection = follow-up.)*

### Issue 184: Auto-zoom / punch-in at peak (opt-in) ✅ DONE (2026-06-22)
**What:** `zoom_on_peak` style flag → brief ~5–10% scale punch-in centered on `peak_s`, returning to 100%. Off by default.
**AC:** visible punch-in at peak; off by default; cites Principle 4. **Src:** 03 / A4.
**Shipped:** `zoom_on_peak` flag (default off) → triangular punch-in (8% / ±0.6s) via ffmpeg `crop`+`scale` using crop's per-frame `t` expression (chose it over `zoompan`, which resamples — `docs/DECISIONS.md` 2026-06-22). Applied before subtitles so captions stay steady. `peak_s` plumbed `Clip.peak_s → worker → render_clip_file`; flag flows through `RenderStyleIn` + the `CaptionStylePanel` "Punch-in at peak" toggle. Cites Principle 4. Tests: +4 in `tests/test_render.py` (applied-in-window / disabled / peak-missing / peak-outside) + endpoint persistence in `tests/test_render_style.py`. Visual review pending the render env.

### Issue 185: Noise reduction (opt-in)
**What:** Optional `arnndn`/`afftdn` denoise pass before loudnorm, off by default.
**AC:** opt-in toggle reduces hiss on a noisy clip without speech artifacts; off by default. **Depends:** 181. **Src:** 03 / C4.

### Issue 186: Creator Brand Kit — saved style applied by default
**What:** Persist a creator-level style (caption style, highlight color, font, background); new renders default from it instead of empty dropdowns; surface in Profile + Editor rail.
**AC:** creator saves a kit; new clips render with it by default; per-creator isolation; per-clip `style_preset` still overrides. **Depends:** 183. **Src:** 03 / B1.

### Issue 187: Learn the Brand Kit from repeated choices (the moat)
**What:** After N consistent style picks, surface "make this your default?" — turn the manual kit into a learned DNA dimension.
**AC:** UI proposes defaulting after N consistent choices; honest framing, no virality. **Depends:** 186 + feedback rows. `[DEC]` (style becomes a learned DNA dimension). **Src:** 03 / B2.

### Issue 188: Timeline + waveform Editor surface (the backbone)
**What:** New Editor page: waveform + synced playhead + transcript aligned under it; selection drives trims/cuts through the existing validate-cuts → `render_cleaned_clip_file` path. No new render primitive. Move Review's transcript/caption/clean panels here; Review keeps trim + triage.
**AC:** waveform/playhead stay in sync; word- and waveform-selection both produce a server-validated cut; fixes the editing-tools-beside-player conflation. **Src:** 03 / C1. *(Full-editor scope per 2026-06-22 decision.)*

### Issue 189: Real per-frame active-speaker reframe
**What:** Replace the single-keyframe static crop (`render.py:179-197`) with per-frame salient-subject tracking (MediaPipe/AutoFlip → time-varying ffmpeg crop, or hosted API).
**AC:** crop follows the active speaker on a moving/two-speaker clip; graceful center-fallback on detection failure. `[DEC]` build-vs-buy (cost + ToS + latency evidence). **Src:** 03 / C2.

### Issue 190: Stream-VOD recap — Part A: data model + budgeted multi-segment selection
**What:** `summaries` artifact (creator_id, video_id, target_duration_s, segments_jsonb, dna_version, render_uri, render_status, status) + a selection step that picks non-overlapping segments under a duration budget and orders them narratively (chapter-aware). Source = **uploaded VOD file (`origin=upload`) only**; no live capture, no YouTube download.
**AC:** `summaries` table + migration + isolation; configurable 5–10 min budget, no overlaps; narrative order; per-segment named principle; eval scenario (budget + setup-start); honest, DNA-grounded, no virality. `[DEC]` (v1 scope expansion — draft entry in finding 01). **Src:** 01 / 185.

### Issue 191: Stream-VOD recap — Part B: 16:9 multi-segment concat render
**What:** Add a 16:9 render path + multi-segment concat (ffmpeg `concat`/`filter_complex`, light transitions) producing the recap mp4; activate the `ClipFormat.horizontal` stub (`models.py:87`); loudness-normalized (181).
**AC:** single horizontal mp4 from ordered segments; Celery task with per-stage `step` events; stored + retention-honored; no regression to 9:16 render (eval green). **Depends:** 190. `[DEC]` (shared with 190). **Src:** 01 / 186 + 03 / C3.

### Issue 192: Stream-VOD recap — Part C: UI surface
**What:** Surface to request a recap from an uploaded VOD, watch it render (stage stepper), review/accept it; honest framing + per-segment principle citations.
**AC:** gated to `origin=upload`; live status via the stepper; FitBadge honesty, never virality; per-segment "why" visible. **Depends:** 190, 191. **Src:** 01 / 187.

### Issue 193: "Your clips are ready" completion notification
**What:** On terminal pipeline `done`, send one transactional email (preference-gated) with the creator's own title + deep link to the per-video map / Review; in-app surface via the notification center.
**AC:** one email per completed job; own-data only (no token/PII); idempotent on retry; unsubscribe + honesty disclaimer. **Depends:** 242 (email infra). **Src:** 01 / 184 (overlaps 11/176c).

### Issue 194: Publish to YouTube — add `youtube.upload` scope + incremental consent
**What:** Add the write scope to `youtube/oauth.py`; existing read-only creators re-consent only on opting into publishing; update `docs/COMPLIANCE.md` scope table.
**AC:** scope requested only for publishing opt-ins (minimum-necessary); tokens Fernet-encrypted, read via `decrypt()`, never logged; Google OAuth verification + **YouTube API compliance audit** tracked as launch dependency. `[DEC]`. **Src:** 13 / D1a. *(D0+D1 scope per 2026-06-22 decision.)*

### Issue 195: `publish_to_youtube` Celery task (`videos.insert`, idempotent)
**What:** Resumable upload of `render_uri` with `#Shorts` description; idempotent on `self.request.id`; stores returned video id before ack. **Pre-audit: forced `private`** (creator publishes manually) until the audit clears.
**AC:** at-least-once redelivery never double-posts; retries transient, surfaces permanent (quota/audit); respects 100-uploads/day bucket; temp media cleaned; no token/PII logged. **Depends:** 194. `[DEC]`. **Src:** 13 / D1b. *(Re-verify the live `videos.insert` quota cost before build — finding 13 flags a discrepancy.)*

### Issue 196: Scheduled publish from the upload-timing window
**What:** `clip_publications` table (status, scheduled_at, platform, published_id); beat sweep enqueues due, creator-confirmed publishes; default `scheduled_at` from `best_upload_windows()`.
**AC:** creator confirms an estimate-framed time (never "go viral"); beat enqueues only due+confirmed rows; failures surfaced; per-creator isolation. **Depends:** 194. `[DEC]`. **Src:** 13 / D1c.

### Issue 197: Wire published clips into the outcome loop
**What:** On publish success set `ClipOutcome.published_youtube_id` so the existing `poll_clip_outcomes` feeds `performed_well` into preference retraining.
**AC:** published clip appears in 48h/7d poll with no new poller code; `performed_well` flows into retraining. **Depends:** 195, 196. **Src:** 13 / D1d.

### Issue 198: Personalization efficacy harness — NDCG/MAP/Kendall (the moat)
**What:** Read-only DB-backed offline eval computing NDCG@5, MAP@5, Kendall τ for three rankings (random, generic-signal, DNA+preference) on a **chronological** held-out split of feedback + outcomes; pooled + per-creator-above-N with bootstrap CIs.
**AC:** chronological split (no leakage); DNA+preference strictly beats random on every metric; beats generic-signal on pooled NDCG@5 (margin gate confirmed at build); real Postgres fixtures, no live APIs. `[DEC]` (metric set, k, split, skip-exclusion). **Src:** 08 / 173a.

### Issue 199: Adversarial clip-quality scenarios + aggregate pass-rate
**What:** Add the 8 geometry scenarios + ≥1 ranking-aware fixture to `tests/eval/scenarios/`; aggregate `scenario_pass_rate`; closes the real "eval harness hardened" pre-launch gate.
**AC:** 8 geometry fixtures each assert their failure mode; ranking fixture asserts DNA-preferred candidate ranks #1; geometry pass-rate 100%; reconcile the gate bookkeeping (`CLAUDE.md:273` vs `PROJECT_STATE.md`). **Depends:** 198. **Src:** 08 / 173b.

### Issue 200: Recency-decay half-life calibration + parameterize
**What:** Compare half-lives {15,30,60,90} on held-out data + a concept-pivot scenario; move the constant to `DECAY_HALF_LIFE_DAYS` (default 30).
**AC:** decayed beats undecayed on the pivot scenario; best half-life reported with CIs; `_LAMBDA` derived from config + `.env.example`. **Depends:** 198. `[DEC]`. **Src:** 08 / 173d.

### Issue 201: `performed_well` baseline-unit fix (Shorts vs long-form)
**What:** Compute the outcome baseline over comparable units (format-matched), not the full-video views median, so the strongest-weighted label isn't systematically negative; re-examine the 3× multiplier vs "strongest label."
**AC:** baseline over comparable-format outcomes; 173a shows the label-bias before/after; multiplier-vs-dominance decision recorded. **Depends:** 198. `[DEC]`. **Src:** 08 / 173e.

### Issue 202: Continuous eval — impression/position logging + standing report
**What:** Log each clip impression with rank + timestamp (missing data for counterfactual eval); emit pooled 173a metrics on each retrain. CI/ratchet mechanics coordinate with Issue 265.
**AC:** impression log (clip_id, rank, shown_at) per creator, isolation-safe; pooled NDCG@5 recomputed per release; no PII/token. **Depends:** 198. `[DEC]` (impression-log schema + retention). **Src:** 08 / 173f.

### Issue 203: Data-gate — unlock delta + real small-catalog path
**What:** Show the *delta to unlock* ("2 more Shorts to unlock Creator DNA") and give sub-threshold creators an honest "clip a video now" path (DNA gates *scoring*, not generation).
**AC:** exact remaining count as a positive next step; working sub-threshold clip CTA with honest "scoring is generic until DNA is built" copy; display predicate aligned to build predicate (no Issue-88 regression); `data_gate_evaluated` event. `[DEC]` if it changes the below-threshold surface. **Src:** 07 / 191.

### Issue 204: Resolve the identity-gate contradiction
**What:** Make onboarding step-3 label and step-4 gate agree — either drop "(optional)" and keep the Issue-100 required gate, or keep it optional and let DNA build from video data alone with identity as enhancer.
**AC:** label + enablement consistent; chosen path works end-to-end; `identity_saved`/`identity_skipped` events. `[DEC]` (re-affirms/reverses Issue-100). **Src:** 07 / 192. *(Decide alongside carry-over Issues 96 + 100.)*

### Issue 205: Stripe ↔ ledger reconciliation Beat task
**What:** Daily beat lists recent `payment_status=paid` Checkout sessions and grants any missing `minute_packs` row (idempotent via `UNIQUE(stripe_session_id)`); alerts on mismatch.
**AC:** grants paid-but-ungranted sessions; re-run is a no-op; persistent mismatch alerts; recorded Stripe fixture in CI. **Src:** 06 / 171b.

### Issue 206: Verify `payment_status` before granting in the webhook
**What:** Guard `routers/billing.py` so `checkout.session.completed` only grants when `payment_status == "paid"`.
**AC:** ignores completed-but-unpaid events; idempotency/RLS unchanged; test covers paid vs unpaid-completed. **Src:** 06 / 171c.

### Issue 207: Stripe Tax on checkout
**What:** Add `automatic_tax` + address collection to the Checkout session, behind a config flag (off in dev/staging until ≥1 tax registration).
**AC:** computes tax from location when on; off preserves current behavior; `.env.example` documents flag + prerequisite. `[DEC]`. **Src:** 06 / 171d.

### Issue 208: Money-refund runbook + truthful ledger entry
**What:** Documented manual refund process in `docs/RUNBOOKS.md`: Stripe-dashboard refund + a compensating negative-minutes ledger row (admin endpoint deferred).
**AC:** covers full + partial refunds and the ledger correction; ledger stays append-only; refund policy in user-facing copy. `[DEC]`. **Src:** 06 / 171e.

### Issue 209: Packaging — per-minute taper rationale + Stream pack
**What:** Resolve the docs contradiction: formally keep per-input-minute, document the taper rationale, add a long-form "Stream" pack; reconcile `COMPETITIVE_RESEARCH.md:113` with the shipped model.
**AC:** pack lineup + taper documented; competitive doc reconciled; no-virality disclaimer kept, no subscription reintroduced; prices hold the margin floor. `[DEC]`. **Src:** 06 / 171f.

## Priority 2 — UI

### Issue 210: Per-video pipeline status stepper on the dashboard
**What:** Replace each video row's single badge with a live stage stepper driven by the existing per-task `step` SSE (ingest/transcribe/signals/render/clean); coarse ETA only; "taking longer than usual" on stale stream; safe one-line reason + Retry/Upload-source on `failed`.
**AC:** subscribes via `useTaskStream`, falls back to badge (observational only); real worker stage labels; no countdown; no virality; `source='ui'` telemetry. **Src:** 01 / 181.

### Issue 211: Global active-tasks panel (supersedes Issue 160)
**What:** `AppChrome`-level floating activity widget showing all in-flight tasks across pages, on a small active-tasks store over the 3-slot SSE cap; resumes across navigation; respects reduced-motion.
**AC:** appears when ≥1 task in-flight, persists across SPA nav; honors the 3-slot cap; empties on terminal state, deep-links to the page; mobile single-column. **Depends:** 210 (shared store). **Closes:** Issue 160. **Src:** 01 / 182.

### Issue 212: Insights page rebuild (carry-over Issue 93)
**What:** Rebuild the bland Insights page into a clear "what this is showing + why it matters" surface. *(Carry-over Issue 93; sequence with Issue 213 to avoid duplicating the per-video view.)*
**AC:** per acceptance criteria in the Issue-93 archive block. **Src:** carry-over 93.

## Priority 3 — UX

### Issue 213: Per-video clips map — source timeline with candidate markers
**What:** Per-video timeline with a marker per candidate (`setup_start_s`→`end_s`, `peak_s` flagged); marker → inline preview + WhyThisClip + named principle + FitBadge; "Review in order" CTA; honest empty-state per `origin`. Add batched `GET /videos/clips/counts` (folds the dashboard N+1, OCB-2) and use it here + on the dashboard.
**AC:** one marker per candidate; preview + rationale + principle + FitBadge, **no raw score, no virality**; honest per-`origin` state (no "row vanishes", Issue-139 lesson); batched counts replaces N+1; deep-link into Review; per-creator isolation. **Src:** 01 / 183 (+ OCB-2). *(Delivers most of carry-over Issue 94.)*

### Issue 214: Onboarding wait UX — labeled stepper + honest microcopy
**What:** Replace the raw `StreamConsole` dumps on catalog-sync + DNA-build with a labeled stage stepper (worker `step` events) + "this takes a few minutes — you can leave and come back" copy. No fabricated ETA.
**AC:** labeled stages + elapsed time, not a log buffer; coarse expectation only; status survives navigation; no virality; `source='ui'` funnel events (ties to 235). **Src:** 07 / 189. *(Shares the stepper component with Issue 210.)*

### Issue 215: Route new creators to onboarding after OAuth
**What:** After an `is_new` OAuth callback, redirect to `/app/onboarding` (not `/`); returning creators still land on the dashboard; keep `EmptyHero` as fallback.
**AC:** first login lands on `/app/onboarding` with sync visibly in progress; returning creators land on dashboard; resolver `next_action_url` agrees; `onboarding_viewed` event. **Depends:** 214. **Src:** 07 / 190.

### Issue 216: Honest personalization-status surface
**What:** Add `personalization: {active, labels, threshold, weight}` to the clips response (`ClipOut`) from `scorer.label_count` + `preference_weight()`; one-line Review UI distinguishing "still learning (N/threshold)" from "personalized."
**AC:** below threshold → `active:false` + honest copy; above → "personalized to your feedback"; no virality; test both bands. `[DEC]` (new honesty surface + API field). **Src:** 08 / 173c.

### Issue 217: Clip-engine transparency — what's NOT clipped (carry-over Issue 94 remainder)
**What:** The "what we passed over and why" half not covered by Issue 213's marker map.
**AC:** per Issue-94 archive block, scoped to the non-selected explanation. **Src:** carry-over 94.

## Priority 4 — Agentic / Caching / Cost management

### Issue 218: Re-enable prompt caching on the repeated-prefix brief endpoints
**What:** titles/hooks/thumbnails/analysis lost their cache breakpoint when the prefix fell below the 2048-token Sonnet floor (Issue 138/140). Raise the shared static+DNA prefix above the floor; re-add a single 1h breakpoint at its end.
**AC:** cached prefix measured >2048 via `count_tokens`; breakpoint at end of stable prefix, volatile content after; test asserts `cache_read_input_tokens>0` on the 2nd same-creator call; `cached_write`/`cached_write_1h` logged. **Src:** 02 / 167b.

### Issue 219: Route clip scoring through the Batch API (−50%)
**What:** Clip scoring is a worker call past the SSE bar; if it tolerates batch latency, route via `client.messages.batches` for a 50% token discount (stacks with caching).
**AC:** spike confirms latency budget; if yes, scoring submits via batches, polls, idempotent + retry-safe; DNA cache prefix preserved; per-video cost halved vs logged usage. **Src:** 02 / 167d.

### Issue 220: Populate the `Usage` cost ledger from every LLM call
**What:** `models.py:664` `Usage` is never written. Write `tokens_in/tokens_out` + cost estimate per creator per period from every LLM call's logged usage, via a single DRY helper. (Merges the duplicate asks in findings 02/167c, 05/169, 06.)
**AC:** every LLM caller increments `Usage` for the owning creator; covered by tests; per-creator isolation; feeds billing + metrics (Issue 237). **Src:** 02 / 167c + 05 / 169 + 06.

### Issue 221: Model-per-task — correct SOT + log the decision
**What:** `docs/SOT.md:16` wrongly says Opus is used for DNA. Correct to reality (Sonnet 4.6 default; Haiku 4.5 for chapters/hooks/analyze-performer); log the deliberate model-per-task choice.
**AC:** SOT LLM row matches code; DECISIONS entry (which task → which model, cost vs quality); note any creator-visible downgrade is gated on Issue 198's eval. `[DEC]`. **Src:** 02 / 167a.

### Issue 222: Tool-result `is_error` flag + chat tool schema `maximum`
**What:** Set `"is_error": true` on failed `tool_result` blocks (`chat/runner.py:103`); add `"maximum": 25` to `get_recent_videos.limit` (`chat/tools.py:58`).
**AC:** failed tool results carry `is_error`; schema advertises the enforced bound; chat isolation/loop tests green. **Src:** 02 / 167e.

### Issue 223: Spike — share the DNA-brief cached block between DNA build and scoring
**What:** DNA build writes a DNA-prefix cache that never reads; scoring writes its own moments later. Investigate a byte-identical, separately-keyed DNA breakpoint so scoring reads what build wrote (within 1h TTL).
**AC:** spike documents feasibility given the differing system instructions; if feasible → follow-up; if not → drop the never-read DNA-build marker. **Src:** 02 / 167f.

## Priority 5 — Security

### Issue 224: Trust-boundary hardening — untrusted content out of `system`, JSON-delimited
**What:** Move `stated_identity`/free-text/titles out of `system` into the user turn (`dna/brief.py`, `knowledge/titles.py`, `knowledge/thumbnails.py`); replace the raw f-string title concat at `routers/insights.py:480` with JSON-encoded data; add a shared JSON-wrap helper used at every prompt-assembly site.
**AC:** no `system` block carries creator free-text/titles (grep + test); analyze-performer passes title as JSON data; cache breakpoints still hit; existing tests green. `[DEC]`. **Src:** 09 / 174a.

### Issue 225: `<untrusted_content_policy>` clause in every system prompt
**What:** One byte-stable shared constant added to the static system prompt of chat, DNA brief, scoring, titles, hooks, thumbnails, improvement, analysis, analyze-performer; names transcripts, titles/descriptions, and web-search results as untrusted.
**AC:** every system prompt carries the clause (test); one shared constant; in the cached prefix; red-team test ("ignore instructions / promise virality" in a transcript doesn't change output). `[DEC]` (minor). **Src:** 09 / 174b.

### Issue 226: Retire or lock down the legacy static UI output sink
**What:** SPA is canonical — either stop serving `static/*.html` + remove the `main.py:136` fallback (preferred), or add a `test_static.py` guard that no LLM-output/title field hits `innerHTML` without `escapeHtml()`.
**AC:** legacy pages unserved OR escaping pinned on every sink; SPA confirmed free of `dangerouslySetInnerHTML`; SOT updated if removed. `[DEC]` if deleting. **Src:** 09 / 174c.

### Issue 227: Honesty guard on generation bodies + ingest length clamp
**What:** Structural/eval assertion that brief/title/hook *bodies* carry no virality-promise language (mirror the chat test); length-clamp + normalize ingested YouTube titles/descriptions.
**AC:** generation-body virality test green; length cap enforced (truncate, not reject); no regression in honesty tests. **Src:** 09 / 174d.

### Issue 228: Per-creator pre-job quota + rate limit on every LLM/render endpoint
**What:** Reusable per-creator quota layer (extend the slowapi `creator_key` pattern) on all render/re-render/scoring/knowledge endpoints — daily cap per op-class + burst limit, configurable. Plus a structural test asserting each LLM/render route carries both a `@limiter.limit` and a `check_balance*` pre-check. Closes the CLAUDE.md pre-launch quota gate.
**AC:** every LLM/render endpoint enforces cap + burst before work; limits in `config.py`/`.env.example`; clean 429 with actionable copy; structural test fails if a new route ships without both gates; no upload-deduct regression. **Src:** 06 / 171a + 04 / I.

### Issue 229: HTTP security-headers middleware
**What:** Middleware emitting CSP (SPA-scoped), HSTS (prod), `X-Frame-Options: DENY`/`frame-ancestors 'none'`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`. (Owns the CSP that finding 09 deferred here.)
**AC:** every app response carries the headers in prod; structural test pins them; CSP doesn't break the SPA. **Src:** 04 / D (+ 09 / Q3).

### Issue 230: CSRF defense-in-depth on state-changing routes
**What:** Fetch-Metadata (`Sec-Fetch-Site`) or double-submit check on all cookie-authed mutating routes, on top of SameSite.
**AC:** cross-site state-changing requests rejected; SPA flows unaffected. `[DEC]` (mechanism choice). **Src:** 04 / F.

### Issue 231: Worker tenant tasks under RLS (stop universal BYPASSRLS)
**What:** Move per-creator worker tasks off `AdminSessionLocal` onto the RLS-gated app role with `app.creator_id` set per task; reserve BYPASSRLS for true cross-tenant sweeps; add child-table RLS policies (`video_metrics`, `retention_curves`, `transcripts`, `signals`, `clip_outcomes`).
**AC:** every tenant worker query runs with the GUC set; integration test: a deliberately-unfiltered worker query returns 0 cross-tenant rows under RLS; sweeps still work. `[DEC]`. **Src:** 04 / A.

### Issue 232: Early `Content-Length` upload rejection + session-revocation note
**What:** Reject oversize uploads on the `Content-Length` header before streaming; document the stateless-session non-revocability tradeoff (or add a short deny-list).
**AC:** oversize upload 413s before streaming; tradeoff documented. **Src:** 04 / K.

## Priority 6 — Observability

### Issue 233: Redaction backstop on the stdout/file log sink
**What:** Key-blocklist scrub in `JsonLogFormatter`/`RequestIDLogFilter` mirroring `event_log._REDACT_SUBSTRINGS`, applied to stdout + `app.log` + the `/api/activity` file sink — structural, not call-site discipline.
**AC:** `log_event("x", email=…, token=…)` emits `[redacted]` for both in JSON mode; DB-sink unchanged; shared/DRY; unit test per substring; Layer-0 green. `[DEC]`. **Src:** 05 / 166.

### Issue 234: Instrument load-bearing surfaces with `log_event`
**What:** Add `log_event` to render/clip pipeline stages, ingestion, billing-webhook receipt/processing, upload-intel — covering the swallowed-exception/anonymous-event classes from the off-course log.
**AC:** each stage emits `_started`/`_done`/`_failed` with `creator_id`+`task_id`; webhook emits received/processed/rejected (no secret); no PII/token (gated by 233); test asserts render-failure emits `*_failed`. **Depends:** 233. **Src:** 05 / 167.

### Issue 235: Funnel instrumentation + resolver/state-machine cleanup
**What:** Route activation-funnel events (oauth→catalog_sync→data_gate→identity→dna→first_video→first_clip→clip_kept) into the queryable `event_log` (`source="backend"`, fixed taxonomy); add trial→first-clip→paid (171g). Repoint `resolve_setup_step` URLs from `/static/*.html` to `/app/*` and remove the dead `awaiting_data` state. (Folds carry-over Issue 161.)
**AC:** each event written with `creator_id` + properties; `clip_kept` = activation; documented activation-rate + median-TTV query; no PII/token; no resolver path lands on a dead `/static/*` page. `[DEC]` (defines the activation event + taxonomy). **Src:** 07 / 188 + 193 + 06 / 171g.

### Issue 236: SLO definitions + first burn-rate alerts
**What:** Define 2 SLOs (API 5xx rate, Celery task-success rate) + one fast-burn page alert each off the metrics already in `observability.py`; document recording rules + routing.
**AC:** `/metrics` actually scraped (config committed); SLO targets in `DEPLOYMENT.md`; alert fires in a synthetic error-injection test; routes to a real channel. `[DEC]` (targets + thresholds, cite SRE Workbook). **Src:** 05 / 168.

### Issue 237: Pipeline + LLM-cost metrics
**What:** Render-failure counter, per-stage Celery duration labels, an LLM token/cost counter (OTel-GenAI labels: provider/model/kind). (The `Usage` ledger write is Issue 220.)
**AC:** Prometheus exposes a token counter by model/kind; render-failure counter present; counts only, no prompt text in labels. `[DEC]` (label schema, align OTel GenAI). **Src:** 05 / 169.

### Issue 238: App-level saturation gauges
**What:** SQLAlchemy pool checked-out gauge, Celery queue depth (Redis `LLEN`), Redis used-memory — the 4th golden signal behind the Redis-down + PgBouncer cascades.
**AC:** three bounded-cardinality gauges on `/metrics`; queue-backlog warning alert; no new connection churn. **Depends:** 236. **Src:** 05 / 170.

### Issue 239: Worker durable log sink
**What:** Pass `log_dir` to the worker's `configure_logging` so worker JSON logs survive restarts like the API's `app.log`.
**AC:** worker writes a rotating JSON log with `request_id` per line; no double-logging when co-hosted. **Src:** 05 / 171.

### Issue 240: Log aggregator (Loki) for the K8s target
**What:** Adopt a log aggregator for the Kubernetes target (recommend Grafana Loki, GCS-backed); collector-side scrub as defense-in-depth.
**AC:** API+worker logs queryable by `request_id`+`creator_id` in one place; collector scrub. `[DEC]` (Loki vs Cloud Logging). **Depends:** 233. **Src:** 05 / 172.

### Issue 241: OpenTelemetry distributed tracing
**What:** OTel tracing on the existing API→Celery propagation: emit W3C `traceparent` alongside `x_request_id`; auto-instrument FastAPI/Celery/SQLAlchemy/httpx; `request_id` as span attribute; head-sample ~10%.
**AC:** a render request yields one trace spanning API→Celery→DB→Anthropic/Voyage/YouTube/R2; `request_id` correlates log↔trace; sampling + export configured; overhead measured. `[DEC]` (revisits the 2026-05-29 "tracing deferred" call). **Src:** 05 / 173.

## Priority 7 — Notifications (supersedes Issues 80 + 81)

### Issue 242: Transactional email infrastructure (Resend) + deliverability
**What:** Resend behind a `notify/mailer.py` typed API with a `NOTIFY_BACKEND=console|resend` dev sink; module-level client; Jinja2 templates; SPF/DKIM/DMARC (`p=none`→tighten) on `autoclip.studio`. **Supersedes Issue 80.**
**AC:** provider/templating/dev-sink logged; typed `send(to,template,context,idempotency_key)`, console-tested; singleton client; `RESEND_API_KEY`/`EMAIL_FROM`/`NOTIFY_BACKEND` in `.env.example`+`SECRETS.md`; DNS records in RUNBOOKS; no test hits live provider. `[DEC]` (new dependency + provider choice). **Src:** 11 / 176a.

### Issue 243: Notification data model + idempotent send task
**What:** Migration for `notification_preferences`, `notification_deliveries`, `notifications`; a `send_notification` Celery task (preference check → dedupe-key row → render → Resend `Idempotency-Key` → in-app row).
**AC:** models + RLS on `notifications`; idempotent under at-least-once (UNIQUE dedupe_key) proven by test; preference check short-circuits, transactional category can't be disabled; no token/PII to provider. **Depends:** 242. `[DEC]` (3 tables + key scheme). **Src:** 11 / 176b.

### Issue 244: Wire transactional triggers to the fan-out (supersedes Issue 81)
**What:** `send_notification.delay(...)` at each terminal fire point: clips ready, DNA built, terminal failure/refund, YouTube re-auth needed, trial ending, balance low.
**AC:** exactly one email + one in-app row per event (dedupe verified); copy passes the honesty check; trial/balance fire from existing beat/ledger paths. **Depends:** 243. **Supersedes:** Issue 81. **Src:** 11 / 176c. *(Delivers Issue 193.)*

### Issue 245: In-app notification center + unsubscribe + preferences UI
**What:** `GET /api/notifications` + `POST /api/notifications/{id}/dismiss`; no-auth `GET /unsubscribe/{token}`; a React Profile preferences pane; List-Unsubscribe (RFC 8058) headers.
**AC:** endpoints enforce isolation (cross-creator read → nothing); SPA renders unread (reuse activity-panel shell); one-click unsubscribe works without login, honored ≤10 business days. **Depends:** 243. **Src:** 11 / 176d.

### Issue 246: Minimal lifecycle sequence (welcome / first-clip nudge / re-engagement)
**What:** Three product-event-triggered lifecycle emails, each unsubscribable, ≤1 per 48h: welcome (first `creator.email` set), no-video nudge (product state), inactivity re-engagement (product state).
**AC:** fire on product state not timers; each carries unsubscribe + physical address (CAN-SPAM), legitimate-interest basis (GDPR); frequency cap; opted-out get none. **Depends:** 244, 245. `[DEC]` (first marketing-class comms; consent posture coordinated with Issue 250). **Src:** 11 / 176e.

## Priority 8 — Privacy / Compliance

### Issue 247: [SEV1] Erasure leak — stop writing deleted-creator PII to `audit_log`
**What:** `DELETE /auth/me` persists `email`+`channel_id` into the never-purged, RLS-exempt `audit_log`. Remove the PII (keep `creator_id`/entity_id, or pseudonymize).
**AC:** deletion audit row has no email/channel_id/PII; integration test confirms; COMPLIANCE updated with the minimization rule. `[DEC]` (cite EDPB CEF 2025). **Src:** 12 / 177a.

### Issue 248: [SEV1] Erasure completeness — purge `event_logs` on deletion
**What:** `event_logs.creator_id` has no FK/CASCADE and lives on a separate engine; deleted-creator telemetry persists forever. Add an explicit cross-engine delete to the deletion path.
**AC:** deletion removes all `event_logs` rows for the creator; integration test (two creators, delete one); best-effort failure doesn't abort deletion; COMPLIANCE data-class table updated. **Src:** 12 / 177b.

### Issue 249: [SEV1] Data export endpoint (Art. 15/20)
**What:** Isolation-safe machine-readable (JSON) export of one creator's data + presigned clip links, async (202+poll) like the improvement brief.
**AC:** authed via `get_current_creator`, single tenant; covers profile/DNA/videos+metrics/feedback/outcomes/chat/billing; clips via presigned links/zip; rate-limited; RLS + app filter (isolation test); Privacy Policy "Your rights" updated. `[DEC]` (format + scope). **Src:** 12 / 177c.

### Issue 250: [SEV2] Retention schedule + missing purge sweeps
**What:** Define + enforce retention for `event_logs` (90d), `audit_log` (counsel-set), inactive-creator tokens/accounts; document the schedule.
**AC:** daily `purge_stale_event_logs` (configurable, default 90); inactive-account policy decided (+ notice-then-delete if adopted); retention table in COMPLIANCE (incl. CCPA disclosure). `[DEC]`. **Src:** 12 / 177d.

### Issue 251: [SEV2] Sub-processor DPAs + Art. 30 record + public list
**What:** Execute/confirm DPAs with all vendors, enable no-train/min-retention switches, publish `docs/SUBPROCESSORS.md`.
**AC:** each vendor DPA on file (Anthropic, Voyage opt-out, Deepgram MIP opt-out if used, R2, Stripe, Google); `SUBPROCESSORS.md` lists name/purpose/data/region/transfer; Voyage zero-retention + Deepgram `mip_opt_out` if hosted; COMPLIANCE references it. **Src:** 12 / 177e.

### Issue 252: [SEV2] Privacy Policy + consent accuracy rewrite
**What:** Make `static/privacy.html` (+ SPA) accurate: sub-processors, audience-demographics disclosure, international transfer, CCPA "do not sell/share", accurate rights (export + corrected deletion claim post-247/248).
**AC:** names sub-processors + transfer + breach contact; CCPA notice + "we do not sell or share"; demographics disclosed; claims match behavior (no over-claim); `test_static.py` pins required clauses. `[DEC]` if a recorded-consent checkbox is added. **Depends:** 247, 248, 249. **Src:** 12 / 177f.

### Issue 253: [SEV2] Breach-notification runbook (Art. 33/34)
**What:** RUNBOOKS entry: detection → 72h authority notify → processor-notify chain → Art. 34 subject notice; templates + contacts.
**AC:** covers the 72h clock, Art. 33(3) content, high-risk subject-notice threshold; processor expectations referenced from DPAs; owner + escalation named. **Src:** 12 / 177g.

### Issue 254: [SEV3] Backup / R2-versioning erasure stance
**What:** Document + verify DB backups and R2 versioning/lifecycle are "put beyond use" + overwritten on a defined cycle so erasure is defensible.
**AC:** COMPLIANCE states the beyond-use + overwrite window; R2 versioning/lifecycle for `source/`+`clips/` documented; no restore re-introduces erased data. `[DEC]` (cite regulator "beyond use"). **Src:** 12 / 177h. *(Coordinate with Issue 258.)*

## Priority 9 — Disaster recovery / Infra / Scale

### Issue 255: Off-box escrow of `TOKEN_ENCRYPTION_KEY` / `JWT_SECRET_KEY` / `.env`
**What:** Out-of-band copy of the irreplaceable secrets into (1) a password manager + (2) GCP Secret Manager so a dead VM doesn't permanently brick encrypted tokens. **Do first.**
**AC:** all three stored in two independent off-box locations (not in git/CI/backup logs); RUNBOOKS rotation gains a re-escrow step; new "DR → key loss" runbook (restore-from-escrow + no-escrow fallback = force re-OAuth). `[DEC]` (Secret Manager as escrow backend). **Src:** 10 / 175a.

### Issue 256: Nightly encrypted Postgres backup to a separate R2 bucket + tested restore
**What:** `scripts/backup_pg.sh` → `pg_dump` → age/gpg encrypt (key from Secret Manager) → upload to `creatorclip-backups`; ~14 daily + 8 weekly; Bucket Lock; in the retention register.
**AC:** encrypted dump in a separate bucket, no secret logged; nightly schedule with success/failure visibility; Bucket Lock ≥14d; executed restore drill (health ok + token decrypts + row counts match, RTO recorded); `.env.example` + COMPLIANCE + RUNBOOKS updated. **Depends:** 255. `[DEC]`. **Src:** 10 / 175b.

### Issue 257: Pre-migration safety dump in the deploy pipeline
**What:** `pg_dump` step in `scripts/deploy.sh` + `deploy.yml` before `alembic upgrade head`; gate the rollout on the dump; keep last N.
**AC:** deploy takes + verifies a dump before migrating, aborts if it fails; rollback note references the dump. **Depends:** 256. **Src:** 10 / 175c.

### Issue 258: R2 durability hardening — Bucket Lock + lifecycle
**What:** Bucket Lock (short retention) on the rendered-clips prefix; lifecycle rule mirroring `SOURCE_MEDIA_RETENTION_HOURS` on source media (defense-in-depth behind the beat purge).
**AC:** Bucket Lock active on clips (in-window delete rejected); lifecycle expires source media in line with retention; documented as belt-and-suspenders. `[DEC]` (R2 has no GA versioning → Bucket Locks chosen). **Src:** 10 / 175d.

### Issue 259: Pool worker DB connections + re-derive the connection budget
**What:** PgBouncer (transaction mode) in front of the worker tier; re-derive the DEPLOYMENT inequality against the real Cloud SQL `max_connections`; pick pool + HPA/KEDA maxima that satisfy it.
**AC:** computed fleet-peak server connections ≤ `max_connections − reserved`; pipeline-soak load test shows no saturation; numbers in DECISIONS + DEPLOYMENT. `[DEC]`. **Src:** 04 / B.

### Issue 260: YouTube Data API quota at scale — extension + fairness + caching
**What:** Submit the quota-extension audit; per-creator fairness sub-budgets so Beat refresh can't starve onboarding; ETag/field-filter/batch caching. (Subsumes carry-over Issue 27.)
**AC:** projected units/day at target creator count within the extended quota; per-creator budget enforced in `youtube/quota.py`; caching reduces measured units/creator; plan in DECISIONS. `[DEC]`. **Src:** 04 / C.

### Issue 261: Define + run the deferred load test to close the gate
**What:** Implement scenarios 1–4 (read-path steady state, pipeline soak, refresh-storm, Redis-degradation) against staging; record p99/pool/quota pass-fail. (Closes carry-over Issue 58 + 112's pending Locust run.)
**AC:** all four green on staging; thresholds + results in DECISIONS; pre-launch load-test gate checked in PROJECT_STATE. `[DEC]`. **Src:** 04 / E.

### Issue 262: Verify token-refresh doesn't pin DB connections under load
**What:** Audit `get_valid_access_token` so the Google round-trip + retry polls don't hold a pooled DB connection; confirm via refresh-storm scenario.
**AC:** refresh path holds no pooled connection across the external call; scenario 3 passes within budget. **Depends:** 261. **Src:** 04 / H.

### Issue 263: Beat + Redis high-availability
**What:** Liveness probe + alert on beat; leader-elected/locked redundant scheduler (RedBeat or equiv.); managed HA Redis with replica.
**AC:** beat outage alerts within minutes (the ToS staleness-purge can't silently stop); Redis failover doesn't cause the opaque-500 cascade. **Src:** 04 / G.

### Issue 264: Reconcile + pin the PgBouncer image; fix token-rotation doc contradiction
**What:** Pin one PgBouncer image digest shared by staging + Helm; verify the RUNBOOKS rotation procedure; flip the stale open gate at `SOT.md:461`.
**AC:** one pinned image; SOT no longer contradicts RUNBOOKS; pre-launch token-rotation gate accurate. **Src:** 04 / J.

## Priority 10 — QA / Release engineering

### Issue 265: Eval gates `clip_engine/` changes as a required CI check
**What:** Run the clip-quality eval as a dedicated CI step; make it a **required** commit status when `clip_engine/` or `tests/eval/` change (via `dorny/paths-filter`); fail if scenario count drops or a scenario is skipped outside an allowlist. Scenario *content* owned by Issue 199.
**AC:** required status on relevant changes; fails below a committed scenario floor; fails on un-allowlisted skip/xfail; no live APIs; ownership seam documented. `[DEC]`. **Src:** 15 / 180a.

### Issue 266: Wire the Playwright SPA harness (smoke + a11y) into CI
**What:** New `ci.yml` job: install Chromium + run `smoke.spec.ts` + `a11y.spec.ts` against the Vite dev server with the mocked backend.
**AC:** job runs both specs (no Docker); a11y fails on any serious/critical axe violation; required (or documented convention); prod-axe stays manual/scheduled. **Src:** 15 / 180b.

### Issue 267: Test-isolation hardening — `pytest-randomly` + conftest cookie fixture + PG fail-fast
**What:** Add `pytest-randomly`; a conftest fixture auto-assigning a fresh per-creator session cookie (or resetting the slowapi Redis bucket); Postgres socket fail-fast mirroring the Redis guard.
**AC:** suite passes under randomized order in CI; the two manual rate-limit workarounds removed; PG fail-fast added; shared engine/event-loop fixtures audited. `[DEC]` (randomized order). **Src:** 15 / 180c.

### Issue 268: Flake detection + quarantine signal (not blanket auto-retry)
**What:** CI-only detection rerun that reports (doesn't silently green) tests passing only on rerun; a `quarantine` marker to keep a known flake visible + non-blocking; documented prohibition on blanket `pytest-rerunfailures` as a merge gate.
**AC:** detection rerun reports rerun-only passes; `quarantine` marker (never `@skip`/delete); policy documented. **Depends:** 267. `[DEC]` (flake policy). **Src:** 15 / 180d.

### Issue 269: Diff/patch-coverage gate + per-module floors
**What:** `diff-cover` patch-coverage on changed lines (`target: auto` style) gating new code without red-walling legacy; per-package floors for `clip_engine/`, `preference/`, `crypto.py`, `limiter.py`, `auth.py`; integrate into `run_layer0.py`/`ci.yml`.
**AC:** patch-coverage check on changed lines; per-package floors; CI and local `/assess` measure identically. `[DEC]`. **Src:** 15 / 180e.

### Issue 270: Migration safety — Squawk + lock/statement timeouts + rollback runbook
**What:** Squawk-lint changed migration SQL in CI; `lock_timeout` + `statement_timeout` for the Alembic run; DEPLOYMENT rollback runbook (image rollback + roll-forward-default policy + expand/contract checklist).
**AC:** unsafe ops fail the lint; a bad migration aborts on timeout; runbook + policy documented. `[DEC]` (Squawk + roll-forward policy). **Src:** 15 / 180f.

### Issue 271: Auto-rollback on failed deploy smoke test
**What:** On smoke failure, deploy re-pulls/`up -d` the previously-running image tag (captured before pull); job still exits non-zero; documented as a stopgap until K8s progressive delivery.
**AC:** prod self-heals to the prior image on smoke failure; failure still visible/alerted; documented. `[DEC]` (single-VM auto-rollback over canary). **Src:** 15 / 180g.

### Issue 272: Visual-regression baselines on stable routes
**What:** `toHaveScreenshot()` on login/pricing/empty-dashboard first; baselines generated in CI (same container), `maxDiffPixelRatio`≈0.01, `animations:'disabled'` + masks, mocked backend; PR job initially non-blocking; baseline updates in their own reviewed PR.
**AC:** screenshots on the stable set; baselines in CI/committed; non-blocking PR job; updates via `--update-snapshots` PR. `[DEC]` (scope + baseline-in-CI policy). **Src:** 15 / 180h.

### Issue 273: Scoped mutation-testing cadence on the load-bearing core
**What:** Configure `mutmut` to target only `clip_engine/`, `preference/`, `crypto.py`, `limiter.py`, and the isolation predicates; manual/scheduled cadence (not per-PR); >80% mutation-score target; triage survivors into test gaps.
**AC:** mutmut scoped to those modules; scheduled run; documented target; survivors triaged. `[DEC]` (scope + gate-vs-report). **Src:** 15 / 180i.

### Issue 274: Test-stack hygiene — httpx2 migration + flow-test robustness
**What:** Migrate the TestClient off the deprecated httpx path (OCB-1); raise the live flow-test timeout / assert on 200 headers rather than rendered output, and investigate whether analysis/title endpoints really exceed ~60s (OCB-3).
**AC:** TestClient deprecation warning gone; flow tests no longer flake on slow LLM; if endpoints exceed ~60s, a perf issue is filed. **Src:** OCB-1 + OCB-3.

---

# Deferred parking lot (explicitly out of v1)

> Filed for traceability; **not** in the active prioritized backlog. Each needs a fresh
> approval (and most a `docs/DECISIONS.md` entry) before promotion.

- **Internationalization / multilingual (entire track)** — *English-only v1 decision, 2026-06-22.*
  Source-language capture (179a), language-aware transcription (179b), supported-language tiers
  + honest degradation (179c), multilingual caption fonts (179d), LLM output-language pinning
  (179e), `defaultAudioLanguage` prior (179f), product-UI i18n scaffold (179g). **Src:** finding 14.
- **Cross-post to TikTok / Reels** — per-platform token model + TikTok draft mode (D2/D3);
  Instagram export-only. Deferred until export adoption proves demand. **Src:** 13 / D2–D3.
- **Web push for "job done"** — VAPID web push as a complementary channel (176f). Defer to
  post-launch. **Src:** 11 / 176f.
- **Cloud SQL automated backups + PITR + HA** — managed-DB DR; belongs to the GKE/Cloud SQL
  cutover, not the single-VM beta (175e). **Src:** 10 / 175e.
- **Livestream auto-recap (subscription perk)** — original carry-over Issue 97 (auto-generate a
  recap from each *live* stream). Distinct from the uploaded-VOD recap now in scope (Issues
  190–192); revisit once live ingestion is on the table (cf. Issue 150 OBS capture).
- **Phase-3 backlog (unchanged)** — thumbnail rendering (DALL-E/SD), vision signals
  (MediaPipe/face-emotion), no-auth demo mode, per-Short mini-editor browse, all-in-one hub
  direction. Full list preserved in `docs/archive/issues_snapshot_2026-06-22.md`.
