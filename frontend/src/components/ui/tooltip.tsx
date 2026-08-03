import * as RadixTooltip from '@radix-ui/react-tooltip'
import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

// Tooltip — supplementary text on hover/focus (Issue 385).
//
// The Provider is mounted INSIDE this component rather than once in AppChrome.
// That costs a provider per tooltip (negligible at the handful of sites we
// migrate) and buys two things: zero app-shell setup, and no way to render a
// trigger outside a provider and have it throw at runtime.
//
// DO NOT bulk-convert the ~87 `title=` attributes in the app. In particular leave
// FitBadge alone: its honesty tooltip has an aria-label fallback that a Radix
// tooltip does not reproduce on touch, where hover does not exist.
//
// A tooltip is never the only source of an affordance's meaning — it sets
// aria-describedby, not aria-label, so the trigger must still be named.
export interface TooltipProps {
  content: ReactNode
  children: ReactNode
  side?: 'top' | 'right' | 'bottom' | 'left'
  className?: string
}

export function Tooltip({ content, children, side = 'top', className }: TooltipProps) {
  return (
    <RadixTooltip.Provider delayDuration={200}>
      <RadixTooltip.Root>
        <RadixTooltip.Trigger asChild>{children}</RadixTooltip.Trigger>
        <RadixTooltip.Portal>
          <RadixTooltip.Content
            side={side}
            sideOffset={6}
            className={cn(
              'z-50 max-w-[260px] rounded-sm border border-strong bg-elevated px-2.5 py-1.5 text-small text-fg shadow-md animate-fade-in',
              className,
            )}
          >
            {content}
            <RadixTooltip.Arrow className="fill-[color:var(--color-elevated)]" />
          </RadixTooltip.Content>
        </RadixTooltip.Portal>
      </RadixTooltip.Root>
    </RadixTooltip.Provider>
  )
}
