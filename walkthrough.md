# How AutoClip works — the 10-minute walkthrough

> *AutoClip predicts fit with your style and audience — it does not promise virality. Every recommendation is an estimate grounded in your own data, not a guarantee. We comply with the YouTube API Services Terms of Service at all times.*

This is the user-facing tutorial document. It walks a new creator from sign-up through their first clip review queue. Skim time: ~5 minutes. Hands-on time: ~10 minutes the first time, then under 2 minutes per video.

---

## TL;DR (the one-paragraph version)

You connect your YouTube channel. We analyse your top and bottom performers, your retention curves, your audience activity windows, and your demographics. We synthesise that into a plain-language brief called your **Creator DNA**. From then on, every video you upload (or link) goes through a pipeline that looks for moments matching your DNA — and clips the **setup**, not just the punchline. You review the clips in a Tinder-style queue: keep, drop, skip, or trim. The more you review, the sharper the model gets.

We never promise virality. The North Star is *audience-fit* — recommendations that match your style and your viewers, scored against your own data.

---

## 1. The 5-minute setup

Visit **`autoclip.studio`** and click **Connect YouTube**. The first time you sign in, you'll see a **5-panel walkthrough** — what AutoClip does, what your DNA is, what a clip is, what the dashboard badges mean, and why we ask you a few quick questions before we start clipping. It takes about 90 seconds.

After the walkthrough lands you on the setup page, you'll see 5 steps:

| Step | What | How long |
|---|---|---|
| 1 | Connect YouTube — OAuth + minimum scopes | 20s |
| 2 | Refresh your channel data — we pull your videos + metrics | 30–60s |
| 3 | Tell us about yourself — niche, audience, hard-nos | 45s (mandatory) |
| 4 | Build your Creator DNA — full analysis + brief | ~30s |
| 5 | Review and confirm — read the brief, edit if needed, confirm | 60s |

Step 3 is now mandatory. Your DNA tells us what your videos have *done*; it can't tell us what you're *trying* to build. Telling us your niche, your audience, and what you won't do means we honour your hard-nos even when an old video accidentally performed well doing something you've moved past.

---

## 2. What your DNA actually contains

Your Creator DNA is a versioned profile. You can rebuild it any time — every rebuild creates a new version; old ones stick around for audit. The one that's `confirmed` is the active brief.

Each DNA captures, in plain language:

- **The hooks your audience stays for** — extracted from top performers' retention curves
- **Your optimal clip length** — not the platform's recommended 60s, *your* sweet spot
- **The best region of a long-form video** to pull Shorts from — first third, middle, last third
- **Your optimal upload gap** between long-form and a derivative Short
- **Pattern fingerprints** — the structural shape of your hooks, the kind of pacing that holds your specific viewers, the topics that converted vs the ones that didn't

You confirm the brief on the **Profile** page. You can edit it any time. The clip engine reads the active DNA on every scoring pass — change it, rebuild, and the next video gets clipped against the new version.

---

## 3. What a clip actually is (the setup principle)

Most AI clippers find a punchline and cut around it. AutoClip doesn't. We use a principle we call **"the setup, not the aftermath."**

When we detect a high-signal moment — laughter, a retention spike, a volume jump — we don't cut at that moment. We look **backwards 60–90 seconds** for the setup. The viewer has to land in context, not in the middle of a punchline that means nothing without it.

Every clip we suggest carries:

- A **score** (mono-typeset number on the player)
- A **principle** — the named clipping principle the engine cited (from `docs/CLIPPING_PRINCIPLES.md`)
- A **reasoning** — the natural-language explanation of why this moment fit your DNA
- The **setup → peak → end** timing line — so you can see exactly what window we picked

In the review queue, hit the **"Why this clip?"** expander to see all of that. The first clip auto-opens it so you learn the affordance; subsequent clips honour whatever you preferred.

---

## 4. The pipeline (what happens after you upload or link)

When you upload a file or link an existing YouTube video, the pipeline runs in the background:

```
ingest → transcribe → audio + retention signals → candidates → score → rank → render
```

- **Ingest** pulls the source video (your upload or yt-dlp from your own channel only)
- **Transcribe** runs word-level transcription (WhisperX by default; Deepgram as a fallback)
- **Signals** extracts audio energy, silence, laughter heuristics, and merges them with the retention curve from YouTube Analytics
- **Candidates** detects peaks and looks backwards to find the setup, producing windows with `setup_start_s`, `peak_s`, `end_s`
- **Score** uses Claude with your DNA brief as a cached system prompt to rank each candidate by fit
- **Rank** applies the preference reranker (recency-decayed; learned from your past feedback once you cross the threshold)
- **Render** cuts the clip and applies a 9:16 vertical reframe with active-speaker centering

You'll see a **live progress stream** on the dashboard via the floating activity panel in the bottom-right corner of every page — Linear-style, Vercel-style. It follows you between pages.

The dashboard badges are plain language:

| Badge | What it means |
|---|---|
| `pending` | Waiting in line — we'll start any second |
| `running` | Ingesting + transcribing + finding signals (~2–5 min on a 20-min video) |
| `done` | Clips are scored; **Generate clips** is your next move |
| `failed` | Something broke; your minutes are automatically refunded |

---

## 5. The review queue

Open **Review**. We show one clip at a time, full-bleed 9:16. Four actions:

- **👍 Keep** — green; high-confidence signal the engine got it right
- **👎 Drop** — red; clip stays in the DB for the audit trail, never resurfaces
- **Skip** — neutral; ambiguous, no signal
- **✂ Trim** — tweak the start/end with the range handles, then save

Below the player is the **"Why this clip?"** expander. Open it any time you want to know what the engine saw — score, cited principle, reasoning, setup timing.

The more you review, the sharper your **preference model** gets. Below your personalization threshold, ranking falls back to your DNA + signals. Once you cross the threshold, your feedback starts re-weighting the queue — recency-decayed, so what you said *last week* matters more than what you said *six months ago*.

---

## 6. Insights — where your stats live

Open **Insights**. Six panels:

1. **Channel snapshot** — videos analysed, longs vs shorts, total minutes processed
2. **Your DNA at a glance** — current version, optimal clip length, best source region, upload gap
3. **Top performers** — the videos that drove your DNA, scored by engagement
4. **Underperformers** — the contrast set; the patterns you've moved past
5. **Best upload windows** — your audience's actual activity (not a generic best-day chart)
6. **Content improvement brief** — Claude does live web research + reads your channel data and writes a personalized strategy memo (~15 seconds; cites specific data rows, never generic advice)

All numeric values render in JetBrains Mono. The data register is intentional — sans for UI, mono for data. Linear, Figma, and Vercel all compose UIs the same way.

---

## 7. Buying minutes

Open **Pricing**. Five packs:

| Pack | Minutes | Price | $/min |
|---|---|---|---|
| Starter | 200 | $18.00 | $0.090 |
| Regular | 500 | $40.00 | $0.080 |
| Creator | 1,000 | $70.00 | $0.070 |
| Pro | 2,000 | $110.00 | $0.055 |
| Studio | 5,000 | $225.00 | $0.045 |

One minute of source video = one minute deducted. No subscription, no expiry. If a video terminally fails ingest, we automatically refund the minutes (compensating-grant ledger entry).

Future: a subscription tier is planned for livestream-recap features (Issue 97 in the backlog).

---

## 8. Coming soon — the OBS hotkey loop

For livestreamers: a small companion app will watch your OBS replay-buffer output folder. When you press OBS's native replay-save hotkey, the file uploads to AutoClip automatically and enters the same pipeline as your other videos.

Architecture B from our research — same pattern as Medal.tv and Outplayed. Backend ships in this monorepo (Issue 95: `creator_api_keys` table + `POST /clips/ingest`); the Go companion app lives in a separate repo and is the next OBS-related milestone.

---

## 9. Honesty constraint (the line we don't cross)

We will never tell you a clip is "guaranteed to go viral." We will never put "virality predictions" in the UI. The North Star is *audience-fit* — and that's an estimate, not a promise.

If you ever see promise-of-virality language anywhere in the app, that's a bug; report it. We have a structural test (`tests/test_compliance_no_virality.py`) that scans every static asset and every OpenAPI response body for forbidden phrases. It blocks every commit and every deploy.

---

## 10. Where to go from here

| Goal | Path |
|---|---|
| See your DNA + edit identity | `/static/profile.html` |
| Generate clips for an existing video | Dashboard → Generate clips button |
| Review the queue | `/static/review.html` |
| Find your stats | `/static/insights.html` |
| Buy minutes | `/static/pricing.html` |
| Read the legal | `/static/tos.html`, `/static/privacy.html` |
| First-run walkthrough (force-replay) | Clear localStorage `creatorclip:walkthrough_seen` then visit `/` |

---

## Appendix — for engineers and partners

If you're a partner / engineer / journalist evaluating AutoClip:

- **Stack**: FastAPI + Celery + Postgres 16 + pgvector + WhisperX + Anthropic Claude (Sonnet 4.6 for clip scoring, Opus 4.7 for DNA synthesis) + Voyage AI embeddings + Cloudflare R2 + Docker Compose (dev) / single-VM with self-hosted GHA runner (prod)
- **Design system**: Linear-style dark + monospace data register. Tokens at `static/_design-tokens.css`. No build step.
- **Compliance**: 30-day partial-staleness purge per YouTube ToS §III.E.4.b. Fernet-encrypted OAuth tokens. Per-creator data isolation enforced at the SQL layer with Postgres RLS as defence-in-depth. Account deletion is right-to-erasure compliant.
- **Source of truth**: `docs/SOT.md`, `docs/DECISIONS.md`, `docs/issues.md`, `docs/PROJECT_STATE.md`, `CLAUDE.md`
- **Last `/assess` verdict**: CONDITIONAL, awaiting PgBouncer load test (Issue 78f) to move to YES. Re-run pending after the most recent batches.

Questions: `reesepludwick@gmail.com`
