# CreatorClip — UI Design System

**Status:** Living source of truth for the React + TypeScript SPA (`frontend/`).
**Established:** 2026-06-18 (Issue 85 — full UI/UX overhaul). See `docs/DECISIONS.md`.

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

### Surfaces
| Token | OKLCH | Utility | Purpose |
|---|---|---|---|
| `--color-bg` | `oklch(8% 0.008 285)` | `bg-bg` | Page background |
| `--color-surface` | `oklch(11% 0.010 285)` | `bg-surface` | Card / panel base |
| `--color-elevated` | `oklch(14% 0.012 285)` | `bg-elevated` | Modals, dropdowns, popovers |
| `--color-raised` | `oklch(17% 0.013 285)` | `bg-raised` | Hover state on surfaces |
| `--color-default` | `oklch(22% 0.012 285)` | `border-default` | Default dividers |
| `--color-strong` | `oklch(30% 0.015 285)` | `border-strong` | Emphasized borders, focus rings |

### Foreground / text
| Token | OKLCH | Utility | Contrast on `bg` | Use |
|---|---|---|---|---|
| `--color-fg` | `oklch(94% 0.008 285)` | `text-fg` | ~14:1 | Body copy, headings |
| `--color-muted` | `oklch(65% 0.010 285)` | `text-muted` | ~5.5:1 | Secondary labels (14px+) |
| `--color-subtle` | `oklch(45% 0.008 285)` | `text-subtle` | ~3.2:1 | Placeholder/disabled **only** |
| `--color-on-accent` | `oklch(98% 0.004 285)` | `text-on-accent` | — | Text on accent fills |

**AA rule:** `text-fg` on any surface passes AA. `text-muted` only at ≥14px.
Never use `text-subtle` for meaningful content.

### Accent (warmer indigo)
| Token | OKLCH | Purpose |
|---|---|---|
| `--color-accent` | `oklch(58% 0.18 280)` | Primary CTA, active state |
| `--color-accent-hover` | `oklch(63% 0.19 278)` | Hover |
| `--color-accent-active` | `oklch(53% 0.17 282)` | Pressed |
| `--color-accent-soft` | `oklch(20% 0.06 280)` | Accent-tinted surface (selected row) |
| `--color-accent-border` | `oklch(35% 0.10 280)` | Focus ring, selected outline |

### Semantic
| Token | OKLCH | Soft fill | Soft border |
|---|---|---|---|
| `--color-success` | `oklch(68% 0.17 145)` | `oklch(18% 0.05 145)` | `oklch(32% 0.09 145)` |
| `--color-warning` | `oklch(75% 0.16 75)` | `oklch(18% 0.05 75)` | `oklch(32% 0.09 75)` |
| `--color-danger` | `oklch(62% 0.20 25)` | `oklch(18% 0.06 25)` | `oklch(32% 0.11 25)` |

---

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

## Spacing — 8pt grid

`--space-1:4px · -2:8px · -3:12px · -4:16px · -5:20px · -6:24px · -8:32px ·
-10:40px · -12:48px · -16:64px`. Every layout gap is a token; no odd values.

## Radii

`--radius-xs:4px (chips) · -sm:6px (buttons/inputs) · -md:8px (cards) ·
-lg:12px (modals) · -xl:16px (player) · -full:9999px (pills)`.

## Motion

Durations: `--duration-instant:80ms · -fast:150ms · -base:220ms · -slow:350ms ·
-spring:500ms`.
Easings: `--ease-standard cubic-bezier(0.2,0,0,1)` (most) ·
`--ease-enter cubic-bezier(0,0,0.2,1)` · `--ease-exit cubic-bezier(0.4,0,1,1)` ·
`--ease-spring cubic-bezier(0.34,1.56,0.64,1)` (clip-card pop, slight overshoot) ·
`--ease-linear` (progress/skeleton).

## Shadows (dark-surface: black-alpha elevation + accent glow)

`--shadow-sm` subtle card lift · `--shadow-md` dropdown/popover ·
`--shadow-lg` modal/drawer · `--shadow-accent-glow` selected/active clip card ·
`--shadow-inset` button top-edge highlight (restores 3D cue flat dark loses).

---

## Accessibility baseline (every page)

- Keyboard navigable; visible focus ring (`--color-accent-border`) on all interactive elements.
- Body text contrast AA (`text-fg`); `text-muted` ≥14px only.
- Honesty disclaimer band visible on every authenticated page (structural test enforces it).
- Mobile-first: dashboards and the clip feed must be usable one-handed on a phone
  (90% of creators check mobile first) — single-column reflow below 640px.
- Respect `prefers-reduced-motion`: spring/entrance animations collapse to instant.
