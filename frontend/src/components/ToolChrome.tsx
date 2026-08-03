import { Outlet } from 'react-router-dom'
import { Nav } from '@/components/Nav'
import { ActivityPanel } from '@/components/ActivityPanel'
import { useAuth } from '@/hooks/useAuth'

/**
 * ToolChrome — route layout for the tool routes (Editor, Review). Issue 389.
 *
 * Sibling of AppChrome, not a variant of it: the two differ in element set (no
 * marketing Footer here), height model (viewport-height vs min-height), overflow
 * (clipped vs visible) and bottom inset. Four forks behind one boolean would be a
 * component doing two jobs, and "no footer on tool routes" would stop being
 * visible in the route tree. AppChrome itself exists because AppLayout was split
 * for exactly this reason (Issue 85b).
 *
 * `h-dvh`, not `h-svh` or `h-screen`. The MDN caution about `dvh` is that content
 * resizes mid-scroll as the URL bar retracts — unreachable here, because at `lg`
 * this shell does not document-scroll, so nothing can trigger that transition.
 * `svh` would permanently reserve the retractable-toolbar height and leave dead
 * background under the docked status bar; `100vh` resolves to the LARGE viewport
 * on mobile Safari, which would push the honesty statement under browser chrome.
 * Below `lg` the shell disengages entirely and the document scrolls as before.
 *
 * Nav is deliberately NOT modified. At `lg` it is a shrink-0 row of a
 * non-scrolling container, so its `sticky top-0` has no scrollport and degrades
 * to static — visually identical, because a row that can never scroll away needs
 * no stickiness. Its mobile-panel growth hazard cannot fire in fixed-height mode
 * either: the hamburger is `sm:hidden`, so it is unreachable at ≥640px.
 */
export function ToolChrome() {
  const { user, balance } = useAuth()
  return (
    <div
      data-tool-chrome
      // --app-bottom-inset lifts the fixed ActivityPanel clear of the docked
      // status bar. In a scrolling document that panel floated over content the
      // user could scroll away from; in a shell that never scrolls it would
      // occlude the honesty statement permanently.
      className="flex min-h-dvh flex-col lg:h-dvh lg:overflow-hidden lg:[--app-bottom-inset:2.25rem]"
    >
      <div className="shrink-0">
        <Nav user={user} balance={balance} />
      </div>
      <Outlet />
      <ActivityPanel />
    </div>
  )
}
