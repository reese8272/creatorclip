import { cva } from 'class-variance-authority'

// Kept in its own module so button.tsx exports only a component
// (react-refresh/only-export-components), matching the repo convention used by
// components/chip/poses.ts.
//
// Exported separately so a router <Link> can wear the button skin without
// nesting a real <button> inside an <a> — nested interactive elements confuse
// assistive tech and swallow keyboard activation. This is the shadcn-standard
// escape hatch; prefer it over <Link><Button/></Link> in any NEW shared
// component (Issue 355).
export const buttonVariants = cva(
  // radius-sm (6px) per docs/UI.md; motion: standard-eased color+shadow+transform
  // with an active:scale press cue; focus ring uses --color-accent-border. The
  // shadow-inset top-edge highlight on filled variants restores the 3D affordance
  // flat dark surfaces lose (docs/UI.md "Shadows").
  'inline-flex items-center justify-center gap-2 rounded-sm font-ui font-medium whitespace-nowrap transition-[background-color,border-color,box-shadow,transform] duration-fast ease-standard active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-border focus-visible:ring-offset-2 focus-visible:ring-offset-bg disabled:pointer-events-none disabled:opacity-50 disabled:shadow-none',
  {
    variants: {
      variant: {
        primary: 'bg-accent text-on-accent shadow-sm shadow-inset hover:bg-accent-hover active:bg-accent-active',
        secondary: 'border border-strong bg-surface text-fg shadow-inset hover:bg-elevated',
        confirm: 'bg-success text-bg shadow-sm shadow-inset hover:opacity-90',
        outline: 'border border-strong bg-transparent text-fg hover:border-accent hover:text-accent-text',
        // `success` / `danger` are the SOFT semantic pair: a low-saturation
        // surface with a semantic border and semantic text, where the icon
        // carries the affordance and colour only reinforces it. Use these for
        // paired choices (Keep / Drop) — a full-bleed saturated fill on both
        // sides makes colour do the icon's job twice over (Issue 384).
        // `confirm` stays full-bleed for single-action modal confirmations,
        // where there is nothing to balance against.
        success:
          'border border-[color:var(--color-success-border)] bg-[color:var(--color-success-soft)] text-success hover:bg-success hover:text-bg',
        danger: 'border border-[color:var(--color-danger-border)] bg-[color:var(--color-danger-soft)] text-danger hover:bg-danger hover:text-bg',
        ghost: 'text-muted hover:text-fg hover:bg-elevated',
      },
      size: {
        default: 'h-9 px-4 text-body',
        sm: 'h-7 px-3 text-small',
      },
    },
    defaultVariants: { variant: 'primary', size: 'default' },
  },
)
