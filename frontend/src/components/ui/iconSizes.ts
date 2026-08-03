import type { LucideIcon } from 'lucide-react'

// Split from icon.tsx so that file exports only components
// (react-refresh/only-export-components) — same convention as buttonVariants.ts.
//
// Sizes are Tailwind `size-*` utilities so icons scale on the same 4px ladder as
// every other box in the app. Do NOT use lucide's `size` prop: it emits inline
// width/height in px and bypasses the scale entirely (Issue 384 acceptance).
export const ICON_SIZE = {
  xs: 'size-3', // 12px — inline with text-label
  sm: 'size-3.5', // 14px — inside size="sm" buttons (h-7)
  md: 'size-4', // 16px — default, inline with text-body
  lg: 'size-5', // 20px — standalone icon buttons
} as const

export type IconSize = keyof typeof ICON_SIZE

// For an icon set inside a RUN OF TEXT rather than a flex row — the trailing
// arrow on a "Refine in editor →" call to action, say. An <svg> is inline-level
// and sits on the baseline, which reads a few pixels low next to lowercase text.
// Harmless inside a flex parent (flex items are blockified and vertical-align is
// ignored), so one treatment is correct everywhere and CTA sites need no
// parent-layout change.
export const ICON_INLINE = 'inline-block align-[-0.15em]'

// Re-exported so call sites that take an icon as a prop can type it without
// importing from 'lucide-react' (which the lint rule forbids).
export type IconComponent = LucideIcon
