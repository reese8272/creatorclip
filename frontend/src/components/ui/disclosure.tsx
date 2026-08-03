import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'
import { ChevronRight } from '@/components/ui/icon'
import { ICON_SIZE } from '@/components/ui/iconSizes'

// Disclosure — a chrome-less detail line (Issue 388).
//
// Deliberately NOT CollapsibleTool: that renders its own card (border + surface +
// shadow), so nesting it inside a panel produces exactly the identical-nested-box
// repetition #400 exists to remove. This is a *detail line*, not a panel, and its
// visual weight should say so.
//
// Native <details>/<summary> because the browser already implements everything
// that matters: aria-expanded, Enter/Space activation, and — in Chrome —
// find-in-page reaching the collapsed content. Its children also stay in the DOM
// when closed, which is why a guard test asserting the honesty wording still
// passes unchanged after the score moves in here.
export interface DisclosureProps {
  summary: ReactNode
  children: ReactNode
  defaultOpen?: boolean
  className?: string
}

export function Disclosure({ summary, children, defaultOpen, className }: DisclosureProps) {
  return (
    <details open={defaultOpen} className={cn('group', className)}>
      <summary className="flex cursor-pointer list-none items-center gap-1 text-small text-muted transition-colors duration-fast hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-border [&::-webkit-details-marker]:hidden">
        <ChevronRight
          className={cn(
            ICON_SIZE.xs,
            'shrink-0 transition-transform duration-fast ease-standard group-open:rotate-90',
          )}
          aria-hidden="true"
        />
        {summary}
      </summary>
      <div className="mt-2">{children}</div>
    </details>
  )
}
