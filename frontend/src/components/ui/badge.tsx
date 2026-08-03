import { cva, type VariantProps } from 'class-variance-authority'
import type { HTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

const badgeVariants = cva(
  'inline-flex items-center gap-1.5 rounded-xs px-2 py-0.5 font-ui text-label font-medium uppercase tracking-[0.08em]',
  {
    variants: {
      variant: {
        muted: 'bg-elevated text-muted',
        accent: 'bg-accent-soft text-accent-text',
        success:
          'bg-[color:var(--color-success-soft)] text-success border border-[color:var(--color-success-border)]',
        warning: 'bg-elevated text-warning',
        danger:
          'bg-[color:var(--color-danger-soft)] text-danger border border-[color:var(--color-danger-border)]',
      },
      // The base is uppercase with wide tracking — correct for a one-word status
      // chip, wrong for a sentence-length label like a clipping principle. Adding
      // the variant beats fighting the base with per-call-site className overrides.
      casing: {
        upper: '',
        sentence: 'normal-case tracking-normal',
      },
    },
    defaultVariants: { variant: 'muted', casing: 'upper' },
  },
)

export interface BadgeProps
  extends HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, casing, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant, casing }), className)} {...props} />
}
