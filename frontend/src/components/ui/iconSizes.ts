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

// Re-exported so call sites that take an icon as a prop can type it without
// importing from 'lucide-react' (which the lint rule forbids).
export type IconComponent = LucideIcon
