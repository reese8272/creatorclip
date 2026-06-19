import { Panel } from '@/components/insights/InsightsPanel'
import type { SavedInsight } from '@/types'

// Bookmarked AI analyses (Issue 117). Hidden entirely when there are none.
export function SavedInsights({
  insights,
  isError,
}: {
  insights: SavedInsight[]
  isError?: boolean
}) {
  // A failed fetch is distinct from "no saved insights yet" (Issue 157): surface it
  // rather than silently rendering nothing (which reads as an empty state).
  if (isError) {
    return (
      <Panel title="Saved insights" sub="Bookmarked AI analyses">
        <p className="text-sm text-danger">Could not load saved insights.</p>
      </Panel>
    )
  }
  if (insights.length === 0) return null
  return (
    <Panel title="Saved insights" sub="Bookmarked AI analyses">
      {insights.map((ins) => (
        <div key={ins.id} className="mb-3 rounded-md border border-default bg-bg p-4 last:mb-0">
          <div className="mb-2 text-sm font-medium text-fg">{ins.title || 'Insight'}</div>
          <div className="text-sm leading-relaxed text-muted">{ins.content}</div>
          <div className="mt-2 font-mono text-xs text-subtle">
            DNA v{ins.dna_version ?? '?'} · {new Date(ins.created_at).toLocaleDateString()}
          </div>
        </div>
      ))}
    </Panel>
  )
}
