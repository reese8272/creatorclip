# Focus Assessment — Chip Animations (mount-status verification)

**Module:** `chip_animations`
**Date:** 2026-06-24
**Scope:** Determine the ACTUAL, CURRENT mount status of each of the 8 Chip animation
states by reading the live code — resolving the documented contradiction between the
design handoff and the project memory/commit `a503ade`.

---

## Contradiction — RESOLVED

| Source | Claim | Verdict vs live code |
|--------|-------|----------------------|
| Handoff `CLAUDE_CODE_INSTRUCTIONS.md` §1 table (lines 44–51) | **3 of 8** mounted; 5 built+tested but in no page | ✅ **CORRECT** — matches the code exactly |
| Commit `a503ade` ("close 10 prototype gaps") + project memory | all 8 wired | ❌ **FALSE** — 5 states render in no real surface, only in `Chip.test.tsx` |

**Method:** grepped each export name across `frontend/src`, excluding `ChipStates.tsx`
(the definition) and `Chip.test.tsx` (a name appearing *only* there = built-but-not-wired).

---

## Per-state table

| # | State | Exported? | Mounted at (file:line) | Intended home (handoff §1/§4) | Match? |
|---|-------|-----------|------------------------|-------------------------------|--------|
| 1 | `ChipThinking` | ✅ ChipStates.tsx:54 | ✅ `pages/Chat.tsx:204` | Chat pre-first-token | ✅ exact |
| 2 | `ChipLookingItUp` | ✅ ChipStates.tsx:103 | ✅ `components/insights/ImprovementBrief.tsx:69` | Improvement-brief web-research | ✅ exact |
| 3 | `ChipLoadingScreen` | ✅ ChipStates.tsx:135 | ✅ `components/AuthGate.tsx:16` | Route/first load | ✅ exact |
| 4 | `ChipPersonalizing` | ✅ ChipStates.tsx:246 | ❌ **NOT MOUNTED** (only `Chip.test.tsx:54`) | Review "still learning" band (`pages/Review.tsx`) | ❌ — Review.tsx:18–24 hand-rolls a static `PersonalizationBand` using a bare `<Chip pose="meditate" size={22}>`, NOT the animated state |
| 5 | `ChipRendering` | ✅ ChipStates.tsx:174 | ❌ **NOT MOUNTED** (only `Chip.test.tsx:52,69`) | Clean-pass render polling (`components/review/CleanPassPanel.tsx`) | ❌ — CleanPassPanel has no Chip and no `progress`; plain text status strings only |
| 6 | `ChipGeneratingClips` | ✅ ChipStates.tsx:218 | ❌ **NOT MOUNTED** (only `Chip.test.tsx:53`) | Dashboard VideoTable generating (`components/dashboard/VideoTable.tsx`) | ❌ — VideoTable uses `StageStepper`/`useStageStream`, not the animated state |
| 7 | `ChipAnalyzing` | ✅ ChipStates.tsx:19 | ❌ **NOT MOUNTED** (only `Chip.test.tsx:47`) | Clip scoring / per-video (`pages/VideoClipsMap.tsx` or scoring state) | ❌ — zero references in VideoClipsMap.tsx or anywhere else; the only mention is the self-comment in ChipStates.tsx |
| 8 | `ChipStreaming` | ✅ ChipStates.tsx:78 | ❌ NOT MOUNTED (only `Chip.test.tsx:49,64`) | Assistant streaming bubble (`pages/Chat.tsx`) | ⚠️ **intentional** — Chat inlines an equivalent `Bubble` (Chat.tsx:267–297) with the same `<Chip pose="think">` + identical `chip-blink 1s steps(1)` caret. Documented A5 "leave as-is". Covered, not a gap. |

**Tally:** 3 mounted · 4 unmounted gaps (SEV1) · 1 intentionally-unused-but-covered (cleanup).

---

## Supporting pieces — all GREEN

**Keyframes (`frontend/src/index.css`):** all present.
- Chip set (lines 243–260): `chip-bob`, `chip-spin`, `chip-scan`, `chip-blink`, `chip-dot`,
  `chip-cardcycle`, `chip-floatup` ✅
- Entrance set (lines 225–238): `fade-in`, `scale-in`, `slide-up` ✅
- No keyframe referenced by a *mounted* state is missing, so no broken-mounted-state SEV1.

**Sprites (`frontend/public/chip/`):** all 10 present — `chip-book/confused/idea/laptop/magnify/
meditate/papers/present/think/wave.png` ✅. `poses.ts` `CHIP_POSES` has exactly 10 keys mapping
1:1 to the sprites (test asserts length 10, Chip.test.tsx:40).

**Base-relative resolution (`Chip.tsx:29`):** `src={`${import.meta.env.BASE_URL}chip/chip-${pose}.png`}`
✅ — correct for the SPA served under `/app/`. Test asserts the `/chip/chip-*.png` suffix (Chip.test.tsx:22).

**prefers-reduced-motion (`index.css:198–204`):** `@media (prefers-reduced-motion: reduce)` targets
`*, *::before, *::after` with `animation-duration: 0.01ms !important; animation-iteration-count: 1 !important`.
The `*` selector collapses every chip-* animation to a single resting frame ✅ — confirms the
ChipStates.tsx header comment that no per-component motion guard is needed.

**Motion parity vs SoT** (`React app visual review/.../Chip Animations (standalone).html` and root
`Chip Animations.dc.html`): EXACT. Every duration/easing/stagger in ChipStates.tsx matches the SoT
byte-for-byte — `bob 2.4/2.5/2.6/2.8/3s ease-in-out`, `spin 1.1s`/`7s linear`, `scan 1.8s ease-in-out`,
`dot 1.2s …/.15s/.3s`, `cardcycle 3s …/.4s/.8s`, `floatup 3/3.4/2.8/3.2s ease-in …/.6s/1.1s/1.7s`,
`blink 1s steps(1)`. The only SoT keyframe NOT ported is `shimmer` — it is demo-card chrome, not part
of the 8 states; correctly excluded. No motion drift.

---

## Findings

### SEV1 — `ChipAnalyzing` built + tested but mounted in no surface
- **Where:** defined `frontend/src/components/chip/ChipStates.tsx:19`; sole reference outside the
  definition is `frontend/src/components/Chip.test.tsx:47`. Intended home `pages/VideoClipsMap.tsx`
  has zero references.
- **Why it matters:** the design intends this mounted at the scoring/per-video "analyzing" surface;
  it currently ships dead. Built-but-not-wired animation = incomplete wiring that needs verification.
- **Fix:** mount centered in the VideoClipsMap scoring/empty branch (handoff A4): `<ChipAnalyzing size={84} />`,
  co-located with the existing textual "scoring…/still analyzing" copy (text stays — Chip is decorative).

### SEV1 — `ChipGeneratingClips` built + tested but mounted in no surface
- **Where:** `ChipStates.tsx:218`; sole external reference `Chip.test.tsx:53`. Intended home
  `components/dashboard/VideoTable.tsx` uses `StageStepper`/`useStageStream` instead.
- **Why it matters:** design intends it in the VideoTable generating state; ships dead.
- **Fix:** render `<ChipGeneratingClips size={44} />` in the row's generating branch (handoff A3), or
  consciously supersede it with StageStepper and delete the dead state — but do not leave it built-yet-dark.

### SEV1 — `ChipRendering` built + tested but mounted in no surface
- **Where:** `ChipStates.tsx:174`; sole external references `Chip.test.tsx:52,69`. Intended home
  `components/review/CleanPassPanel.tsx` has no Chip and tracks render via plain text status strings.
- **Why it matters:** CleanPassPanel already owns a real "rendering" state (`applying` flag after
  `applyClean()`); the design intends the animated determinate bar here. Ships dead.
- **Fix:** render `<ChipRendering progress={pct} label="Rendering cleaned cut" pose="laptop" size={72} />`
  while `applying && !cleanedUri` (handoff A2).

### SEV1 — `ChipPersonalizing` built + tested but mounted in no surface
- **Where:** `ChipStates.tsx:246`; sole external reference `Chip.test.tsx:54`. Intended home
  `pages/Review.tsx` hand-rolls `PersonalizationBand` (Review.tsx:18–24) with a static
  `<Chip pose="meditate" size={22}>` instead of the animated state.
- **Why it matters:** design intends the animated meditating/floating-binary state in the
  still-learning band; the animated component ships dead while a non-animated stand-in is used.
- **Fix:** in the still-learning (`status.active === false`) branch, render `<ChipPersonalizing>` once
  in a small centered block (handoff A1 — mind the 150×200 footprint to avoid band layout thrash).

### cleanup — `ChipStreaming` intentionally unused, but is a real DRY duplicate
- **Where:** `ChipStates.tsx:78` vs the inline `Bubble` in `Chat.tsx:267–297`.
- **Why:** Chat deliberately inlines an equivalent (same `pose="think"` Chip + identical
  `chip-blink 1s steps(1)` caret) per handoff A5 "leave as-is". This is a *documented* decision and is
  functionally covered — NOT a wiring gap. But the caret markup is duplicated in two places (ChipStates
  ChipStreaming and Chat Bubble); if Chat is the canonical streaming surface, either adopt
  `ChipStreaming` or delete it to kill the dead second copy. No behavior risk.

### cleanup — stale "all 8 wired" claim in commit/memory
- Commit `a503ade` message and project memory assert all 8 states are wired; the live code shows 3.
  Update the memory/state note so future sessions don't trust the false claim. (Handoff doc is accurate
  and can stand as the SoT for wiring status.)

---

## Verdict

**NEEDS-WORK.** No BLOCKER (no mounted state is broken; all sprites/keyframes/reduced-motion/base-path
are correct and motion parity is exact). But **4 of 8 animation states (ChipAnalyzing, ChipGeneratingClips,
ChipRendering, ChipPersonalizing) are built + tested yet mounted in no real surface** despite the design
intending each mounted — 4× SEV1 incomplete wiring. ChipStreaming is intentionally covered (cleanup).
The handoff's "3 of 8" table is the accurate record; the commit/memory "all 8 wired" claim is wrong and
should be corrected.
