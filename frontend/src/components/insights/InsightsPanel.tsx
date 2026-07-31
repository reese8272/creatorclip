import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

// Panel chrome shared by every insights section (header + optional sub/aside).
//
// `id` makes a panel addressable (Issue 355). Proof of lift, the originality
// guard and the channel fingerprint are the landing page's headline claims but
// had no URL to link to. They stay panels rather than routes — their reading
// order on this page is load-bearing — so a hash anchor is the right handle.
// scroll-mt clears the sticky nav so the heading isn't parked under it.
export function Panel({
  id,
  title,
  sub,
  aside,
  children,
}: {
  id?: string
  title: ReactNode
  sub?: ReactNode
  aside?: ReactNode
  children: ReactNode
}) {
  return (
    <section
      id={id}
      className={cn(
        'mb-5 rounded-md border border-default bg-surface p-5 shadow-sm shadow-inset',
        id && 'scroll-mt-20',
      )}
    >
      <div className="mb-4 flex items-baseline justify-between gap-3">
        <h3 className="flex items-center gap-2 text-h3 font-ui text-fg">{title}</h3>
        {sub && <span className="text-small text-muted">{sub}</span>}
        {aside}
      </div>
      {children}
    </section>
  )
}

// Mono data cell used in the channel-totals and DNA grids. Recessed (bg-bg) so
// the stat reads as inset into the panel surface above it.
export function Cell({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="rounded-md border border-default bg-bg px-4 py-3">
      <div className="text-label uppercase tracking-[0.08em] text-muted">{label}</div>
      <div className="mt-1 font-mono text-lg font-semibold text-fg">{value}</div>
    </div>
  )
}

export const gridCls = 'grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-3'
