import { useState, type ReactNode } from 'react'

// A labelled disclosure section for the review-page tools (Why this clip,
// Caption style, Clean pass). Replaces the vanilla icon-rail + slide-out drawer
// with a straightforward player-first collapsible (Issue 85f redesign).
export function CollapsibleTool({
  title,
  defaultOpen = false,
  plain = false,
  children,
}: {
  title: ReactNode
  defaultOpen?: boolean
  // plain = normal-case h3 header (e.g. "Why this clip" with the Chip, Issue 306);
  // default = the uppercase tracked label used by the editor tool sections.
  plain?: boolean
  children: ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="rounded-md border border-default bg-surface shadow-sm shadow-inset">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className={
          plain
            ? 'flex w-full items-center justify-between px-[18px] py-3.5 text-h3 font-semibold text-fg'
            : 'flex w-full items-center justify-between px-4 py-3 text-sm font-medium uppercase tracking-[0.06em] text-muted hover:text-fg'
        }
      >
        {title}
        <span className="font-mono text-accent-text">{open ? '−' : '+'}</span>
      </button>
      {open && <div className="border-t border-default p-[18px]">{children}</div>}
    </div>
  )
}
