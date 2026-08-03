import type { ReactNode } from 'react'
import { ToolStatusBar } from '@/components/layout/ToolStatusBar'
import { cn } from '@/lib/utils'

/**
 * ToolShell — the body of a tool route (Editor, Review). Issue 389.
 *
 * Owns the single <main> landmark and the docked status bar. Pages render this
 * on EVERY branch, including loading, error and picker states, so the honesty
 * statement is structurally impossible to omit.
 *
 * WHY THIS IS A COMPONENT AND NOT PART OF ToolChrome
 * -------------------------------------------------
 * Editor.test.tsx and Review.test.tsx render <Editor/> / <Review/> standalone —
 * inside a MemoryRouter with NO layout element — and assert the disclaimer is
 * present. That is an acceptance criterion of Issue 389 ("existing structural
 * test asserting the disclaimer on tool routes stays green"). Hoisting the status
 * bar into the ToolChrome route layout looks like a tidy-up and would turn those
 * tests red. It must stay inside the page's own component tree.
 *
 * HEIGHT CONTRACT (documented in docs/UI.md)
 * ------------------------------------------
 * At `lg` the shell does not scroll; panels do. Every flex level between the
 * viewport-height ancestor and a scroller needs `min-h-0`, because flex
 * auto-minimum-size is content-based per level — ONE omission anywhere in the
 * chain silently defeats the whole thing and reads as "the page is cut off".
 * Below `lg` nothing is height-constrained and the document scrolls as before.
 */
export function ToolShell({
  children,
  /**
   * `true` (default): <main> is itself one scroll region — the right shape for
   * loading, error and picker branches whose content is a single column.
   * `false`: <main> clips, and the children own their own [data-tool-scroll]
   * regions — the workspace shape.
   */
  scroll = true,
  className,
}: {
  children: ReactNode
  scroll?: boolean
  className?: string
}) {
  return (
    <div data-tool-shell className="flex flex-1 flex-col lg:min-h-0">
      <ToolStatusBar />
      <main
        className={cn(
          'flex-1 lg:min-h-0',
          scroll ? 'lg:overflow-y-auto' : 'lg:overflow-hidden',
          className,
        )}
      >
        {children}
      </main>
    </div>
  )
}

/**
 * ToolMain — the pre-389 centred measure, for use INSIDE ToolShell.
 *
 * Loading, error and empty states have no workspace to lay out; keeping them at
 * the old `max-w-5xl` centred width means they look exactly as they did before
 * the shell landed, rather than stretching a one-line message across 1440px.
 */
export function ToolMain({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('mx-auto w-full max-w-5xl px-4 py-10', className)}>{children}</div>
}
