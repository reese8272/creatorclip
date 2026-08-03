import { cva } from 'class-variance-authority'

// Kept in its own module so select.tsx exports only components
// (react-refresh/only-export-components) — same convention as buttonVariants.ts.
//
// Sizes mirror buttonVariants exactly (h-9 default / h-7 sm) so a Select and a
// Button sitting in the same row line up without per-call-site correction.
export const selectTriggerVariants = cva(
  'inline-flex w-full items-center justify-between gap-2 overflow-hidden whitespace-nowrap rounded-sm border border-strong bg-bg text-left text-fg transition-[background-color,border-color,box-shadow] duration-fast ease-standard hover:bg-elevated focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-border focus-visible:ring-offset-2 focus-visible:ring-offset-bg disabled:pointer-events-none disabled:opacity-50 data-[placeholder]:text-subtle',
  {
    variants: {
      size: {
        default: 'h-9 px-3 text-body',
        sm: 'h-7 px-2.5 text-small',
      },
    },
    defaultVariants: { size: 'default' },
  },
)
