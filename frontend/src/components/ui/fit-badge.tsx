import { cva, type VariantProps } from 'class-variance-authority'
import type { HTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

// The "fit with your channel style" badge — the visible form of the CLAUDE.md
// honesty constraint and the differentiator vs. opaque virality scores
// (docs/UI.md "Confidence badges"). Three tiers only; NEVER "viral" or
// "predicted performance". Raw scores are never shown. Every badge carries the
// honesty tooltip. Consumes the --color-fit-* tokens (previously unused).
export type FitTier = 'strong' | 'moderate' | 'exploratory'

const FIT_TOOLTIP = "Based on your channel's content DNA — not a guarantee of performance."

const LABELS: Record<FitTier, string> = {
  strong: 'Strong channel fit',
  moderate: 'Moderate channel fit',
  exploratory: 'Exploratory',
}

const fitBadgeVariants = cva(
  'inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 font-ui text-label font-medium tracking-[0.04em]',
  {
    variants: {
      tier: {
        strong: 'bg-fit-strong-soft text-fit-strong',
        moderate: 'bg-fit-moderate-soft text-fit-moderate',
        exploratory: 'bg-fit-exploratory-soft text-fit-exploratory',
      },
    },
    defaultVariants: { tier: 'exploratory' },
  },
)

const DOT: Record<FitTier, string> = {
  strong: 'bg-fit-strong',
  moderate: 'bg-fit-moderate',
  exploratory: 'bg-fit-exploratory',
}

export interface FitBadgeProps
  extends HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof fitBadgeVariants> {
  tier: FitTier
}

export function FitBadge({ className, tier, ...props }: FitBadgeProps) {
  return (
    <span
      className={cn(fitBadgeVariants({ tier }), className)}
      title={FIT_TOOLTIP}
      aria-label={`${LABELS[tier]}. ${FIT_TOOLTIP}`}
      {...props}
    >
      <span className={cn('size-1.5 rounded-full', DOT[tier])} aria-hidden="true" />
      {LABELS[tier]}
    </span>
  )
}
