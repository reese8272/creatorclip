import type { ReactNode } from 'react'

// Panel chrome shared by every insights section (header + optional sub/aside).
export function Panel({
  title,
  sub,
  aside,
  children,
}: {
  title: string
  sub?: ReactNode
  aside?: ReactNode
  children: ReactNode
}) {
  return (
    <section className="mb-5 rounded-md border border-default bg-surface p-5">
      <div className="mb-4 flex items-baseline justify-between gap-3">
        <h3 className="text-md font-medium text-fg">{title}</h3>
        {sub && <span className="text-xs text-subtle">{sub}</span>}
        {aside}
      </div>
      {children}
    </section>
  )
}

// Mono data cell used in the channel-totals and DNA grids.
export function Cell({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="rounded-md border border-default bg-bg px-4 py-3">
      <div className="text-xs uppercase tracking-[0.06em] text-subtle">{label}</div>
      <div className="mt-1 font-mono text-lg font-semibold text-fg">{value}</div>
    </div>
  )
}

export const gridCls = 'grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-3'
