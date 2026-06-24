# ui_parity — assessed 2026-06-24

Scope: prototype-parity sweep of the live React app (`frontend/src/pages/*`,
`frontend/src/components/*`) against the design source of truth —
`AutoClip App (standalone).html`, its `README.md` (design notes + token table),
and `CLAUDE_CODE_INSTRUCTIONS.md` (§5 per-screen checklist, §6 do-not-regress,
§8 tokens). Static read only — no browser render; visual-only judgments are
marked (needs-runtime-confirmation).

## Findings

- [SEV1] pages/VideoClipsMap.tsx:117 (whole component) — the per-video clip-map
  authed route (`/app/video/:videoId`) renders **no `DisclaimerBand`**, while all
  nine main nav pages do. CLAUDE.md mandates the honesty band on *every* authed
  page and the brief scores an absent band as SEV1. Mitigating: the page is a
  Task-C surface (no equivalent in the standalone), carries honest inline framing
  in its empty state ("These estimates are grounded in your own data — not a
  guarantee of performance.", line 104) and a `not.toMatch(/virality/i)` test, and
  its FitBadges carry the honesty title — so no virality claim is made; only the
  *consistent band surface* is missing | fix: render
  `<DisclaimerBand>AutoClip predicts fit with your style and audience — it does not
  promise virality. All scores are estimates grounded in your own channel
  data.</DisclaimerBand>` at the top of the returned `<main>` (above the header),
  and add it to the existing VideoClipsMap honesty test.

- [SEV2] components/ActivityPanel.tsx:114,170 — uses Tailwind classes
  `bg-surface-raised` and `border-border` whose backing tokens are **undefined**
  in `index.css` `@theme` (the defined tokens are `--color-raised` and
  `--color-default`/`--color-strong`; there is no `--color-surface-raised` or
  `--color-border`). In Tailwind v4 an unmapped color utility yields no rule, so
  the task rows render with **no background** and the panel with **no border** —
  drift from the design system. Pre-existing (Issue 211), Task-C surface, not a
  standalone-parity element | fix: change `bg-surface-raised` → `bg-raised` and
  `border-border` → `border-default` (the panel already sets `bg-surface`).
  (needs-runtime-confirmation — verify the rows are actually unstyled in a build.)

- [SEV2] components/Footer.tsx:13 — footer reads only "© AutoClip 2026" and links
  Terms/Privacy/Accessibility; the standalone footer reads "© 2026 AutoClip — fit
  estimates, never virality promises." (Privacy · Terms · Docs). The honesty
  *tagline* "fit estimates, never virality promises" was dropped. Honesty is still
  satisfied via the per-page `DisclaimerBand`, so this is reinforcing-copy drift,
  not a missing constraint | fix: append the tagline to the `©` span, e.g.
  `© AutoClip 2026 — fit estimates, never virality promises.`

- [cleanup] Task-A animation wiring incomplete — only **3 of 8** `ChipStates`
  exports are mounted in real pages (verified by import grep): `ChipThinking`
  (Chat), `ChipLookingItUp` (ImprovementBrief), `ChipLoadingScreen` (AuthGate).
  The other five are built + unit-tested but mount nowhere:
  `ChipPersonalizing` (intended: Review still-learning band — Review.tsx still uses
  a static `<Chip pose="meditate">`, A1), `ChipRendering` (CleanPassPanel applying
  window still shows only a status string, A2), `ChipGeneratingClips` (VideoTable
  generate wait, A3), `ChipAnalyzing` (VideoClipsMap scoring/empty state, A4),
  `ChipStreaming` (Chat — A5, intentionally left as-is per the handoff). This is
  **not drift from the visual SoT**: the App-standalone is a static prototype that
  doesn't show these animated states (they live in the separate
  `Chip Animations (standalone).html`), and the app's static screens match. It is
  the handoff's own Task-A wiring work left undone (CLAUDE_CODE_INSTRUCTIONS §1
  documents the same "3 of 8" pre-state) | fix: complete A1–A4 per §4 of
  CLAUDE_CODE_INSTRUCTIONS (each is a localized presentational mount; A5 stays).

- [cleanup] Raw OKLCH literals that duplicate an existing token (README §8: "map
  the literal back to the utility"): LongFormEditor.tsx:36/37 `TIER_TEXT.strong`
  `oklch(72% 0.16 145)` == `--color-fit-strong` and `.moderate` `oklch(78% 0.14 75)`
  == `--color-fit-moderate`; ChipStates.tsx:125,278 `oklch(72% 0.16 145)` ==
  `--color-fit-strong`. (The fit-tier *segment* fills in LongFormEditor.tsx:31-33
  and the filmstrip-frame gradients in TrimFilmstrip.tsx are alpha-variant /
  per-frame values with no exact token — leave those.) | fix: replace the exact
  duplicates with `var(--color-fit-strong)` / `var(--color-fit-moderate)`.

- [cleanup] Settings.tsx:152-153 — highlight-color swatch mocks use raw
  `oklch(75% 0.16 75)` (yellow) and `oklch(68% 0.17 145)` (green) that are **not**
  in the token table. These are decorative preview swatches in a "Soon" row, so
  there is no token to map to; acceptable, but if a caption-highlight palette is
  ever tokenized these should adopt it. No action required now.

## Per-screen parity (§5 checklist)

| Screen | DisclaimerBand | Chip poses per spec | Copy/layout | Verdict |
|---|---|---|---|---|
| Nav | n/a | n/a | sticky+blur pill nav, active=`bg-accent-soft text-accent-text`, brand "AutoClip", channel·balance pill·Logout, hamburger <640px, warning balance on `low_balance` | match |
| Dashboard | ✅ exact | — | "Your videos" h1 + "{n} videos · {clips} clips rendered · {channel}", +Link/Analyze actions, `grid-cols-[minmax(0,1fr)_296px]`, Review-queue + Analytics + Creator-DNA sidebar, EmptyHero | match |
| Review | ✅ exact | meditate (band, static), think (why), laptop (open-in-editor) | two-col, `TrimFilmstrip` dual-handle (replaces 2 sliders), YourCall card, "Open in the editor" exact copy | match (A1 anim unwired) |
| Editor | ✅ exact | think (short callout), papers (transcript), magnify+idea (long via LongFormEditor), confused (empty) | mode toggle ▮Short/▭Long, short=player+Timeline+transcript+cuts+Caption/CleanPass, long=MasterTimeline(fit-tier segs)+Suggested+transcript+Chapters/Export | match |
| Profile | ✅ (rich copy) | book (DnaCard) | read-only snapshot, DNA card (Signature traits, Re-sync/View full DNA), Saved analyses, Library, YouTube 28d, "Editing settings →" | match |
| Settings | ✅ exact | — | 5 card sections (Captions/Cuts/Tone/Workflow/Brand kit) w/ segmented+toggle mocks, honest "Soon" badges, Reset/Save footer | match |
| Insights | ✅ exact | idea (header), present (brief), magnify-ring via ChipLookingItUp | ChannelSnapshot, DnaSnapshot, Performer top/bottom, WhatChanged, UploadWindows, ImprovementBrief, Saved | match |
| Analyze | ✅ exact | magnify (header) | query card + 2×2 tools (Title/Hook/Chapters/Thumbnails) | match |
| Assistant | ✅ exact | wave (empty), think (avatar), ChipThinking (pre-token) | empty "Ask about your channel" + suggestions, streaming bubble + chip-blink caret | match (A5 left as-is, per spec) |
| Pricing | ✅ exact | — | minute packs, featured Creator `shadow-accent-glow` + "Most picked" | match |

## Task-C do-not-regress (§6) — all present, none simplified away

| Surface | State |
|---|---|
| Login (`pages/Login.tsx`) | present (real OAuth hero; "audience-fit over generic virality") |
| Onboarding (`pages/Onboarding.tsx` + `onboarding/*`) | present (identity + DNA stream console) |
| Walkthrough (`pages/Walkthrough.tsx`) | present (5-panel first-run tour) |
| VideoClipsMap (`pages/VideoClipsMap.tsx`) | present — but missing DisclaimerBand (SEV1 above) |
| ActivityPanel (`components/ActivityPanel.tsx`) | present — token-class bug (SEV2 above) |
| Mobile hamburger / responsive | present (Nav `sm:hidden` panel) |
| Real data states (polling / skeletons / EmptyHero / TrialBanner / DnaCta / LowBalanceWarning) | present (Dashboard wires all) |

## Honesty constraints

- DisclaimerBand on every authed page: **9/9 main nav pages ✅**, VideoClipsMap ✗
  (SEV1). Band copy matches the standalone variants per surface.
- Virality wording: **zero** positive claims anywhere. All 61 "virality" mentions
  are honesty-framed ("does not promise virality", "not a generic virality score",
  "audience-fit over generic virality"); only negative-assertion tests exist.
- FitBadge tiers (strong/moderate/exploratory) used as the headline fit signal in
  Review, Editor, VideoClipsMap, LongFormEditor; tier colors map to `--color-fit-*`.
- Chip sprites: 10/10 PNGs present in `frontend/public/chip/`, matching the pose
  registry (`components/chip/poses.ts`). Chip is `alt=""`/`aria-hidden` (a11y
  deviation from handoff `alt="Chip"`, logged in DECISIONS — correct per W3C).

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | n/a (presentational sweep) |
| 2 Concurrency & scale | n/a |
| 3 Security & compliance | ok — honesty: 1 SEV1 (band absent on VideoClipsMap); no virality claim anywhere |
| 4 Clip-quality | n/a (not a clip-scoring module) |
| 5 Anthropic SDK | n/a |
| 6 Cleanliness & typing | 3 cleanup (OKLCH literals duplicating tokens; A-state wiring incomplete) |
| 7 Error handling / API | n/a (no router surface) |
| 8 Config & paths | ok — Chip src is BASE_URL-relative; sprites present |

## Module verdict
NEEDS-WORK — the app is a high-fidelity, faithful port of the standalone across all
10 §5 screens with every Task-C surface intact and zero virality wording; the one
real honesty gap is the missing `DisclaimerBand` on the authed VideoClipsMap route
(SEV1, easy fix), plus an ActivityPanel undefined-token visual bug and dropped
footer tagline (SEV2) and minor token/wiring cleanup.
