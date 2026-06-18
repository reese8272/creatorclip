import { cva, type VariantProps } from 'class-variance-authority'
import type { HTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

const badgeVariants = cva(
  'inline-flex items-center gap-1.5 rounded-sm px-2 py-0.5 text-2xs font-medium uppercase tracking-[0.06em]',
  {
    variants: {
      variant: {
        muted: 'bg-elevated text-muted',
        accent: 'bg-accent-soft text-accent',
        success:
          'bg-[color:var(--color-success-soft)] text-success border border-[color:var(--color-success-border)]',
        warning: 'bg-elevated text-warning',
        danger:
          'bg-[color:var(--color-danger-soft)] text-danger border border-[color:var(--color-danger-border)]',
      },
    },
    defaultVariants: { variant: 'muted' },
  },
)

export interface BadgeProps
  extends HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />
}
