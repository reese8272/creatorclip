import type { ReactNode } from 'react'

// Panel chrome shared by every insights section (header + optional sub/aside).
export function Panel({
  title,
  sub,
  aside,
  children,
}: {
  title: ReactNode
  sub?: ReactNode
  aside?: ReactNode
  children: ReactNode
}) {
  return (
    <section className="mb-5 rounded-md border border-default bg-surface p-5 shadow-sm shadow-inset">
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
