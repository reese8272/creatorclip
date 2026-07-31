import { Link } from 'react-router-dom'
import { cn } from '@/lib/utils'

export type AskSurface = 'assistant' | 'analyze' | 'insights'

// Issue 355 finding 2: three surfaces answer "why did this happen / what should
// I do next", and from the nav a first-run creator could not tell them apart.
//
// They are NOT merged, because the axis that separates them is real: scope and
// interaction model, not "AI-ness". Assistant is channel-wide and conversational
// (multi-turn SSE, regenerate/stop); Analyze is one video and tool-shaped
// (one-shot task stream); Insights is channel-wide and automatic (no question
// asked). Unifying Assistant and Analyze would mean one input box driving two
// protocols and a merged transcript model — a redesign, not a naming pass.
//
// What was actually duplicated was the ENTRY POINT: reaching /analysis from the
// nav showed only a URL box plus a free-text question, because the four
// generators are gated on ?video_id= — i.e. a strictly worse Assistant. That nav
// entry is gone; this row is how /analysis stays discoverable, and it explains
// all three in one place instead of three.
const SURFACES: { key: AskSurface; label: string; blurb: string; to: string }[] = [
  {
    key: 'assistant',
    label: 'Assistant',
    blurb: 'Ask about your whole channel',
    to: '/chat',
  },
  {
    key: 'analyze',
    label: 'Analyze one video',
    blurb: 'Why did this video perform that way?',
    to: '/analysis',
  },
  {
    key: 'insights',
    label: 'Insights',
    blurb: 'Auto-generated report, no question needed',
    to: '/insights',
  },
]

export function AskSurfaceTabs({ current }: { current: AskSurface }) {
  return (
    <nav
      aria-label="Ways to ask"
      className="mb-6 grid gap-2 sm:grid-cols-3"
    >
      {SURFACES.map((s) => {
        const isCurrent = s.key === current
        return (
          <Link
            key={s.key}
            to={s.to}
            aria-current={isCurrent ? 'page' : undefined}
            className={cn(
              'rounded-md border px-3 py-2 transition-colors duration-fast ease-standard',
              isCurrent
                ? 'border-accent-border bg-accent-soft'
                : 'border-default bg-surface hover:bg-elevated',
            )}
          >
            <span
              className={cn(
                'block text-small font-medium',
                isCurrent ? 'text-accent-text' : 'text-fg',
              )}
            >
              {s.label}
            </span>
            <span className="block text-xs text-subtle">{s.blurb}</span>
          </Link>
        )
      })}
    </nav>
  )
}
