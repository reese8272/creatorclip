import { useState, type ReactNode } from 'react'

// A labelled disclosure section for the review-page tools (Why this clip,
// Caption style, Clean pass). Replaces the vanilla icon-rail + slide-out drawer
// with a straightforward player-first collapsible (Issue 85f redesign).
export function CollapsibleTool({
  title,
  defaultOpen = false,
  children,
}: {
  title: string
  defaultOpen?: boolean
  children: ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="rounded-md border border-default bg-surface shadow-sm shadow-inset">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center justify-between px-4 py-3 text-sm font-medium uppercase tracking-[0.06em] text-muted hover:text-fg"
      >
        {title}
        <span className="font-mono text-accent">{open ? '−' : '+'}</span>
      </button>
      {open && <div className="border-t border-default p-4">{children}</div>}
    </div>
  )
}
