import { Panel } from '@/components/insights/InsightsPanel'
import type { UploadIntel } from '@/types'

export function UploadWindows({
  intel,
  isError,
}: {
  intel: UploadIntel | undefined
  isError?: boolean
}) {
  // A failed fetch is distinct from a genuine "no data yet" empty state (Issue 157).
  if (isError) {
    return (
      <Panel title="Best upload windows">
        <p className="text-sm text-danger">Could not load timing data.</p>
      </Panel>
    )
  }
  if (!intel || !intel.data_available || intel.best_windows.length === 0) {
    return (
      <Panel title="Best upload windows">
        <p className="text-sm italic text-subtle">
          Not enough audience activity data yet. Connect your channel and sync data first.
        </p>
      </Panel>
    )
  }
  return (
    <Panel title="Best upload windows">
      {intel.best_windows.map((w, i) => (
        <div
          key={i}
          className="flex items-center justify-between border-b border-default py-2 text-sm last:border-b-0"
        >
          {/* `label` already includes the day name (upload_intel/timing.py),
              so render it alone — prepending day_name double-printed it
              ("Friday Friday 12:00 PM"). */}
          <span className="text-fg">{w.label}</span>
          <span className="font-mono font-semibold text-accent">
            {(w.activity_index * 100).toFixed(0)}%
          </span>
        </div>
      ))}
      {intel.optimal_gap_hours != null && (
        <div className="mt-5 border-t border-default pt-5">
          <div className="mb-2 text-xs uppercase tracking-[0.06em] text-muted">
            Optimal gap between long-form → Short
          </div>
          <div className="font-mono text-xl font-semibold text-success">
            {intel.optimal_gap_hours.toFixed(1)}h
          </div>
        </div>
      )}
    </Panel>
  )
}
