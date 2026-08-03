import { cva } from 'class-variance-authority'

// Kept in its own module so tabs.tsx exports only components
// (react-refresh/only-export-components) — same convention as buttonVariants.ts.
//
// Lifted verbatim from the hand-rolled TAB_BASE / TAB_ACTIVE / TAB_IDLE constants
// that lived in pages/Editor.tsx, so the Radix swap is a behaviour fix with no
// visual change. Active and idle carry identical padding so selecting a tab never
// shifts layout — the same rule Nav.tsx follows for its active pill.
export const tabsTriggerVariants = cva(
  'inline-flex items-center gap-1.5 rounded-sm px-3 py-1.5 text-small font-medium transition-colors duration-fast ease-standard focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-border focus-visible:ring-offset-2 focus-visible:ring-offset-bg disabled:pointer-events-none disabled:opacity-50 data-[state=active]:bg-accent-soft data-[state=active]:text-accent-text data-[state=inactive]:text-muted data-[state=inactive]:hover:text-fg',
)
