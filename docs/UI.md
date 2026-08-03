# CreatorClip — UI Design System

**Status:** Living source of truth for the React + TypeScript SPA (`frontend/`).
**Established:** 2026-06-18 (Issue 85 — full UI/UX overhaul). See `docs/DECISIONS.md`.
**Polish pass 1 (2026-06-19):** `index.css` reconciled to this spec (radii ladder, semantic
type scale, Geist app-shell font) and the system applied to the shared primitives (Card, Panel,
Button, Badge, **FitBadge**, Modal, Nav) — elevation/shadow/motion/confidence tokens now consumed.
**Reconciliation + elevation rules (2026-08-03, Issue 400a):** five values in this
doc had drifted from `index.css` and were corrected **in the CSS's favour**, per the
"fix the mismatch, do not fork" rule below — the surface ladder, the two border
tokens, `--color-subtle` (value, contrast figure *and* usage rule), and
`--color-accent` (plus the missing `--color-accent-text` row). A dead `--ease-linear`
line was deleted, the unenforceable "no odd values" spacing rule rewritten around the
scale that actually generates utilities, and the shadow section updated for the
`--shadow-inset` → `--inset-shadow-highlight` composition fix. New **Elevation** and
**Hierarchy** sections make the four-level ladder a rule new screens inherit rather
than a set of tokens nobody used. See DECISIONS 2026-08-03.
**Polish pass 2 / per-page sweep (2026-06-19):** deferred items landed — `FitBadge` mounted on the
Review clips (fit-tier thresholds in `frontend/src/lib/fit.ts`; **tunable** — see DECISIONS),
`shadow-accent-glow` on the active clip + featured pricing pack, `text-h1/h2` on page titles,
`animate-fade-in`/`slide-up` entrances. Every token group now has consumers (verified by audit).
See DECISIONS 2026-06-19 (pass 1 + pass 2).

This document is the **design** source of truth. The **implementation** source of
truth is the Tailwind v4 `@theme` block in `frontend/src/index.css` — every value
below lives there as a CSS custom property. When they disagree, fix the mismatch;
do not fork.

> **Scope boundary (strangler-fig).** These tokens style the React SPA only. The
> legacy vanilla pages (`static/*.html`) still read `static/_design-tokens.css`
> and are intentionally untouched until each page ports to React. Do not edit one
> expecting the other to change.

---

## Direction

We **evolve** the dark, indigo-accented Linear-style aesthetic (Issue 99) — we do
not abandon it. Three deliberate pivots distinguish CreatorClip from generic AI
clip tools:

1. **Warmer, OKLCH-built palette.** Neutrals carry a faint blue-violet warmth
   (hue 285) instead of cold steel gray; the accent shifts to a warmer, higher-
   chroma violet. OKLCH gives perceptually-even steps and predictable AA contrast
   on dark surfaces.
2. **Player-first clip experience.** The primary product surface treats the
   AI-generated clips *as the product* — a vertical, scrollable, player-first feed
   ("feels like scrolling", per the PRD), not a generic data dashboard. (Applied
   in the dashboard/review page issues.)
3. **Honest confidence badges.** A three-tier "**fit with your channel style**"
   badge system — never a virality score. This is the visible form of the
   `CLAUDE.md` honesty constraint and the differentiator vs. Opus Clip's opaque
   score.

**Sources:** Linear 2026 refresh; Vercel Geist; Material Design 3 motion;
OKLCH-for-dark-mode (UX Collective, LogRocket); AI-confidence UX patterns
(aiuxdesign.guide, DesignKey). Full link set in `docs/DECISIONS.md` (2026-06-18).

---

## Color (OKLCH)

Token names match the `@theme` (utilities derive from them: `--color-surface` →
`bg-surface`, `--color-muted` → `text-muted`, `--color-default` →
`border-default`).

### Surfaces — the four-level elevation ladder
| Level | Token | OKLCH | Utility | Role |
|---|---|---|---|---|
| **L0** | `--color-bg` | `oklch(7% 0.008 285)` | `bg-bg` | Page canvas — **and** recessed wells *inside* a panel (transcript scroll box, cut queue, timeline gutter), bounded by `border-default` |
| **L1** | `--color-surface` | `oklch(13% 0.011 285)` | `bg-surface` | The default card. **Secondary** content. Hover → L2 |
| **L2** | `--color-elevated` | `oklch(16.5% 0.013 285)` | `bg-elevated` | The **one dominant panel per screen**, and transient surfaces floating over L1 (select menu, popover, tooltip). Hover → L3 |
| **L3** | `--color-raised` | `oklch(20% 0.014 285)` | `bg-raised` | Modal / dialog — floats over everything, including open L2 menus. Also the hover state of an L2 surface |
| — | `--color-default` | `oklch(26% 0.014 285)` | `border-default` | Default dividers |
| — | `--color-strong` | `oklch(34% 0.016 285)` | `border-strong` | Emphasised borders, focus rings, and the border of an L2 primary panel |

Values re-tuned 2026-06-19 (a card cleared the page by only ~3% L, which is
imperceptible); this table was corrected to match `index.css` on 2026-08-03.

### Foreground / text
| Token | OKLCH | Utility | Contrast on `bg` | Use |
|---|---|---|---|---|
| `--color-fg` | `oklch(94% 0.008 285)` | `text-fg` | ~14:1 | Body copy, headings |
| `--color-muted` | `oklch(65% 0.010 285)` | `text-muted` | ~5.5:1 | Secondary labels (14px+) |
| `--color-subtle` | `oklch(62% 0.009 285)` | `text-subtle` | ~5:1 | Legitimate tertiary text (timestamps, counts) |
| `--color-on-accent` | `oklch(98% 0.004 285)` | `text-on-accent` | — | Text on accent fills |

**AA rule:** all three text tokens meet AA (4.5:1) on the page background.
`--color-subtle` was raised from 45% — which failed at 2.7:1 — per the live-site
audit (Issue 165). It is now legitimate for tertiary text, but content the
creator must actually READ still belongs on `text-muted` or `text-fg`: the fix
was to contrast, not to hierarchy.

### Accent (warmer indigo)
| Token | OKLCH | Purpose |
|---|---|---|
| `--color-accent` | `oklch(54% 0.18 280)` | **Solid** CTA background — tuned dark enough that `--color-on-accent` clears 4.5:1 on it |
| `--color-accent-text` | `oklch(72% 0.15 280)` | Accent-coloured **text** on dark. The solid value above is too dark to satisfy this, so one token cannot do both — they pull opposite ways (Issue 165, Radix's solid-vs-text convention) |
| `--color-accent-hover` | `oklch(59% 0.19 278)` | Hover |
| `--color-accent-active` | `oklch(49% 0.17 282)` | Pressed |
| `--color-accent-soft` | `oklch(20% 0.06 280)` | Accent-tinted surface (selected row) |
| `--color-accent-border` | `oklch(35% 0.10 280)` | Focus ring, selected outline |

### Semantic
| Token | OKLCH | Soft fill | Soft border |
|---|---|---|---|
| `--color-success` | `oklch(68% 0.17 145)` | `oklch(18% 0.05 145)` | `oklch(32% 0.09 145)` |
| `--color-warning` | `oklch(75% 0.16 75)` | `oklch(18% 0.05 75)` | `oklch(32% 0.09 75)` |
| `--color-danger` | `oklch(62% 0.20 25)` | `oklch(18% 0.06 25)` | `oklch(32% 0.11 25)` |

---

## Elevation — which surface a container takes

One rule, four levels (see the Surfaces table above for the values):

| Container | Level | Utility |
|---|---|---|
| Page canvas | L0 | `bg-bg` |
| A recessed well *inside* a panel — scroll region, timeline gutter, queue | L0 | `bg-bg` + `border-default` |
| The ordinary card / panel — **secondary** content | L1 | `bg-surface` |
| The **one** dominant panel on the screen | L2 | `bg-elevated` + `border-strong` |
| Floating over a panel: select menu, popover, tooltip | L2 | `bg-elevated` |
| Modal / dialog — over everything, including open menus | L3 | `bg-raised` |

**Hover lifts exactly one rung.** L1 → L2, L2 → L3. Never skip.

**Luminance alone is not enough.** L1 → L2 is a 3.5-point step; on its own it will
not make a panel dominant. A primary panel moves **four cues together**:

1. surface one rung up (`bg-surface` → `bg-elevated`),
2. `border-strong` instead of `border-default`,
3. more internal padding, at `rounded-lg` instead of `rounded-md`,
4. a `text-h3` header instead of the `text-label` uppercase overline secondaries use.

`components/ui/card.tsx` encodes this as `level="panel" | "primary"` — use the
prop rather than reassembling the four cues by hand.

**Known exception.** Badges and chips (`ui/badge.tsx`) use `bg-elevated` as an
inline *tint*, not as a surface — they sit one step above their host. On an L2
host they tie. A translucent tint token would fix it properly; deferred.

## Hierarchy — what dominates a screen

- **Exactly one primary panel per screen.** It is the thing the page exists to do:
  the player on Review, the viewer on the Editor. Everything else is L1.
- **Secondary panels recede.** The strongest lever is not a class — it is being
  **collapsed by default**. A rail of four identically-weighted open cards gives
  the eye no entry point and forces a linear scan, which is what "blocky" means.
- **Body copy sits on the semantic type scale.** Content the creator must read
  never uses `text-[10px]`, and never uses `text-subtle` (see the AA rule).
- Timecodes use `font-mono text-mono` — the token exists for exactly this.

## Confidence badges — "fit with your channel style"

Three tiers. **Never** "viral" or "predicted performance." Tooltip on every
badge: *"Based on your channel's content DNA — not a guarantee of performance."*
Raw scores are never shown; tier thresholds are a product decision.

| Tier | Label | Pill bg | Pill text | Dot |
|---|---|---|---|---|
| Strong | "Strong channel fit" | `oklch(20% 0.06 145)` | `oklch(72% 0.16 145)` | `oklch(68% 0.17 145)` |
| Moderate | "Moderate channel fit" | `oklch(20% 0.05 75)` | `oklch(78% 0.14 75)` | `oklch(75% 0.16 75)` |
| Exploratory | "Exploratory" | `oklch(17% 0.010 285)` | `oklch(55% 0.010 285)` | `oklch(45% 0.010 285)` |

---

## Typography

| Font | Token | Role |
|---|---|---|
| Geist Sans | `--font-ui` | App chrome: nav, labels, badges, buttons, forms, tables |
| Inter | `--font-display` | Page titles (h1), onboarding/marketing headings |
| Geist Mono | `--font-mono` | Timecodes, IDs, code |

Rule of thumb: inside the app shell → Geist; greeting the user (splash/onboarding) → Inter.

| Token | Size | Line-height | Weight | Font | Use |
|---|---|---|---|---|---|
| `--text-h1` | 2.25rem | 1.15 | 650 | Inter | Page title, hero |
| `--text-h2` | 1.5rem | 1.25 | 600 | Inter | Section heading |
| `--text-h3` | 1.125rem | 1.35 | 600 | Geist | Card title |
| `--text-body` | 0.875rem | 1.55 | 400 | Geist | Body copy |
| `--text-small` | 0.75rem | 1.5 | 400 | Geist | Captions, metadata |
| `--text-label` | 0.6875rem | 1.4 | 500 | Geist | Badge text, overlines |
| `--text-mono` | 0.8125rem | 1.6 | 400 | Geist Mono | Timecodes, IDs |

Letter-spacing: h1 `-0.025em`, h2/h3 `-0.015em`, label `+0.04em` (all-caps `+0.08em`).

---

## Spacing

Spacing comes from **Tailwind's default `--spacing` scale** (0.25rem base), i.e.
`p-2` = 8px, `gap-3` = 12px, `py-6` = 24px. Prefer even steps.

> **The `--space-*` tokens in `@theme` generate NOTHING.** Tailwind v4 derives
> spacing utilities from the `--spacing` namespace, not `--space-*`, so those ten
> declarations are reachable only as `var(--space-4)` inside an arbitrary value —
> which no file does. Treat them as dead until deleted; do not add more.

An arbitrary pixel value (`p-[18px]`, `py-[5px]`) is a smell — use the nearest
step. Several predate this rule and are being migrated as surfaces are touched.

## Radii

`--radius-xs:4px (chips) · -sm:6px (buttons/inputs) · -md:8px (cards) ·
-lg:12px (modals) · -xl:16px (player) · -full:9999px (pills)`.

## Motion

Durations: `--duration-instant:80ms · -fast:150ms · -base:220ms · -slow:350ms ·
-spring:500ms`.
Easings: `--ease-standard cubic-bezier(0.2,0,0,1)` (most) ·
`--ease-enter cubic-bezier(0,0,0.2,1)` · `--ease-exit cubic-bezier(0.4,0,1,1)` ·
`--ease-spring cubic-bezier(0.34,1.56,0.64,1)` (clip-card pop, slight overshoot).
`--ease-snappy cubic-bezier(0.4,0,0.2,1)`.
Note `--duration-*` are the only motion tokens with hand-written `@utility` rules
(`duration-instant/fast/base/slow`); `duration-spring` has a token but no utility,
so writing it produces nothing.

## Shadows (dark-surface: black-alpha elevation + accent glow)

`--shadow-sm` subtle card lift · `--shadow-md` dropdown/popover ·
`--shadow-lg` modal/drawer · `--shadow-accent-glow` selected/active clip card.

`--inset-shadow-highlight` (utility `inset-shadow-highlight`) is the top-edge
catch-light that restores the 3D cue flat dark loses — **the load-bearing
elevation cue**, since black drop shadows barely register on near-black.

> It was `--shadow-inset` until 2026-08-03, in the same namespace as
> `--shadow-sm` — so `shadow-sm shadow-inset` **did not compose**: both write
> `--tw-shadow` and the second wins. The highlight was silently dropped at ~30 of
> its 43 call sites, which is most of why cards read flat. It now lives in v4's
> inset-shadow namespace (`--tw-inset-shadow`) and composes. Pair them freely.

---

## Accessibility baseline (every page)

- Keyboard navigable; visible focus ring (`--color-accent-border`) on all interactive elements.
- Body text contrast AA (`text-fg`); `text-muted` ≥14px only.
- Honesty disclaimer band visible on every authenticated page (structural test enforces it).
- Mobile-first: dashboards and the clip feed must be usable one-handed on a phone
  (90% of creators check mobile first) — single-column reflow below 640px.
- Respect `prefers-reduced-motion`: spring/entrance animations collapse to instant.
