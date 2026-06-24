import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { Analytics, AnalyticsPeriod } from '@/types'

const PERIODS: { value: AnalyticsPeriod; label: string }[] = [
  { value: '7d', label: 'Last 7 days' },
  { value: '28d', label: 'Last 28 days' },
  { value: '90d', label: 'Last 90 days' },
  { value: 'all', label: 'All time' },
]

function fmtNum(n: number | null): string {
  if (n == null) return '—'
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

function Cell({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-default bg-bg px-4 py-3">
      <div className="text-xs uppercase tracking-[0.06em] text-subtle">{label}</div>
      <div className="mt-1 font-mono text-lg font-semibold text-fg">{value}</div>
    </div>
  )
}

// A single label/value row for the compact sidebar variant.
function MetricRow({ label, value, top }: { label: string; value: string; top?: boolean }) {
  return (
    <div className={`flex justify-between py-[7px] text-small ${top ? 'border-t border-default' : ''}`}>
      <span className="text-muted">{label}</span>
      <span className="font-mono font-semibold text-fg">{value}</span>
    </div>
  )
}

export function AnalyticsPanel({ variant = 'panel' }: { variant?: 'panel' | 'sidebar' }) {
  const [period, setPeriod] = useState<AnalyticsPeriod>('28d')
  const { data, isPending, isError } = useQuery({
    queryKey: ['analytics', period],
    queryFn: () => api<Analytics>(`/creators/me/insights/analytics?period=${period}`),
  })

  const periodSelect = (
    <select
      aria-label="Analytics period"
      value={period}
      onChange={(e) => setPeriod(e.target.value as AnalyticsPeriod)}
      className="rounded-sm border border-strong bg-bg px-2 py-1 text-xs text-muted"
    >
      {PERIODS.map((p) => (
        <option key={p.value} value={p.value}>
          {p.label}
        </option>
      ))}
    </select>
  )

  // Sidebar variant (Issue 305 — Dashboard videos-first): recompacted as a
  // vertical metric list in its own card, with a "Full insights →" link.
  if (variant === 'sidebar') {
    return (
      <div className="rounded-md border border-default bg-surface p-[18px] shadow-sm shadow-inset">
        <div className="mb-3 flex items-center justify-between">
          <div className="text-label uppercase tracking-[0.08em] text-muted">YouTube Analytics</div>
          {periodSelect}
        </div>
        {isPending ? (
          <p className="text-small text-subtle">Loading…</p>
        ) : isError ? (
          <p className="text-small text-subtle">Could not load analytics.</p>
        ) : !data?.metrics_available ? (
          <p className="text-small text-subtle">No analytics yet — connect your channel and sync.</p>
        ) : (
          <div className="flex flex-col">
            <MetricRow label="Views" value={fmtNum(data.total_views)} />
            <MetricRow label="Watch time" value={`${data.total_watch_time_h.toFixed(1)}h`} top />
            <MetricRow
              label="Avg view"
              value={data.avg_view_duration_s != null ? `${data.avg_view_duration_s.toFixed(0)}s` : '—'}
              top
            />
            <MetricRow
              label="Engagement"
              value={
                data.avg_engagement_rate != null
                  ? `${(data.avg_engagement_rate * 100).toFixed(1)}%`
                  : '—'
              }
              top
            />
          </div>
        )}
        <Link
          to="/insights"
          className="mt-3 inline-block text-small text-accent-text hover:underline"
        >
          Full insights →
        </Link>
      </div>
    )
  }

  return (
    <div className="rounded-md border border-default bg-surface p-5 shadow-sm shadow-inset">
      <div className="mb-4 flex items-center justify-between">
        <div className="text-label uppercase tracking-[0.08em] text-muted">YouTube Analytics</div>
        {periodSelect}
      </div>

      {isPending ? (
        <p className="text-sm text-subtle">Loading…</p>
      ) : isError ? (
        <p className="text-sm text-subtle">Could not load analytics.</p>
      ) : !data?.metrics_available ? (
        <p className="text-sm text-subtle">
          No analytics data yet — connect your channel and sync.
        </p>
      ) : (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(160px,1fr))] gap-3">
          <Cell label="Views" value={fmtNum(data.total_views)} />
          <Cell label="Watch time" value={`${data.total_watch_time_h.toFixed(1)}h`} />
          <Cell
            label="Avg view duration"
            value={data.avg_view_duration_s != null ? `${data.avg_view_duration_s.toFixed(0)}s` : '—'}
          />
          <Cell
            label="Engagement rate"
            value={
              data.avg_engagement_rate != null
                ? `${(data.avg_engagement_rate * 100).toFixed(1)}%`
                : '—'
            }
          />
          <Cell label="Videos tracked" value={String(data.videos_in_period)} />
        </div>
      )}
    </div>
  )
}
