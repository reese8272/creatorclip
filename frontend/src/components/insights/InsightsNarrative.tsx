// InsightsNarrative — "what this is showing + why it matters" framing (Issue 212).
//
// This component answers the three questions the Insights page must address:
//   1. What's working (top performers + why, grounded in your own DNA + score components)
//   2. What's not working (underperformers + why)
//   3. What changed since last week (7d vs 28d analytics diff)
//
// Design notes:
//   - Honesty constraint: no virality promises; all claims cite specific video rows
//     or named channel metrics, not generic "expert" advice.
//   - The week-over-week diff is computed from the two existing analytics endpoints
//     (/insights/analytics?period=7d and /insights/analytics?period=28d). No new
//     backend field required.
//   - The "why" copy is derived purely from the backend-computed performance_score_components
//     already present in the InsightsResponse payload — no extra LLM call here.
//     On-demand deep AI analysis remains available via the Analyze button in PerformerPanel.

import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { Panel } from '@/components/insights/InsightsPanel'
import type { Analytics, InsightsResponse } from '@/types'

// ── What-Changed diff ────────────────────────────────────────────────────────

interface DiffRow {
  label: string
  current: number
  prior: number
  formatFn: (n: number) => string
}

function pct(current: number, prior: number): number | null {
  if (prior === 0) return null
  return ((current - prior) / prior) * 100
}

function DiffChip({ value }: { value: number | null }) {
  if (value == null) return <span className="text-subtle font-mono text-xs">—</span>
  const up = value >= 0
  return (
    <span
      className={`font-mono text-xs font-semibold ${up ? 'text-success' : 'text-danger'}`}
      aria-label={`${up ? 'up' : 'down'} ${Math.abs(value).toFixed(0)}%`}
    >
      {up ? '▲' : '▼'} {Math.abs(value).toFixed(0)}%
    </span>
  )
}

function DiffTable({ rows }: { rows: DiffRow[] }) {
  return (
    <table className="w-full text-sm" aria-label="Week-over-week channel metric changes">
      <thead>
        <tr className="border-b border-default">
          <th className="py-2 text-left text-xs uppercase tracking-[0.06em] text-muted font-normal">
            Metric
          </th>
          <th className="py-2 text-right text-xs uppercase tracking-[0.06em] text-muted font-normal">
            Last 7 days
          </th>
          <th className="py-2 text-right text-xs uppercase tracking-[0.06em] text-muted font-normal">
            vs 28-day avg
          </th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.label} className="border-b border-default last:border-b-0">
            <td className="py-2 text-muted">{r.label}</td>
            <td className="py-2 text-right font-mono font-medium text-fg">
              {r.formatFn(r.current)}
            </td>
            <td className="py-2 text-right">
              <DiffChip value={pct(r.current, r.prior / 4)} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// We compare the 7d total against the per-week equivalent of the 28d total
// (i.e. 28d ÷ 4 = one-week average). This is "what changed vs your typical week"
// rather than "what changed vs last week specifically" — which we can't compute
// without a snapshot store. [DEC] noted in DECISIONS.md.
export function WhatChanged() {
  const q7 = useQuery({
    queryKey: ['analytics', '7d'],
    queryFn: () => api<Analytics>('/creators/me/insights/analytics?period=7d'),
  })
  const q28 = useQuery({
    queryKey: ['analytics', '28d'],
    queryFn: () => api<Analytics>('/creators/me/insights/analytics?period=28d'),
  })

  if (q7.isPending || q28.isPending) {
    return (
      <Panel
        title="What changed this week"
        sub="7-day vs your 28-day average"
      >
        <p className="text-sm italic text-subtle">Loading…</p>
      </Panel>
    )
  }

  if (q7.isError || q28.isError || !q7.data?.metrics_available) {
    return (
      <Panel title="What changed this week" sub="7-day vs your 28-day average">
        <p className="text-sm italic text-subtle">
          Not enough analytics data yet — sync your channel to unlock this view.
        </p>
      </Panel>
    )
  }

  const d7 = q7.data
  const d28 = q28.data!

  const rows: DiffRow[] = [
    {
      label: 'Views',
      current: d7.total_views,
      prior: d28.total_views,
      formatFn: (n) => n.toLocaleString(),
    },
    {
      label: 'Watch time',
      current: d7.total_watch_time_h,
      prior: d28.total_watch_time_h,
      formatFn: (n) => `${n.toFixed(1)}h`,
    },
    ...(d7.avg_view_duration_s != null && d28.avg_view_duration_s != null
      ? [
          {
            label: 'Avg view duration',
            current: d7.avg_view_duration_s,
            prior: d28.avg_view_duration_s,
            formatFn: (n: number) => `${n.toFixed(0)}s`,
          },
        ]
      : []),
    ...(d7.avg_engagement_rate != null && d28.avg_engagement_rate != null
      ? [
          {
            label: 'Engagement rate',
            current: d7.avg_engagement_rate,
            prior: d28.avg_engagement_rate,
            formatFn: (n: number) => `${(n * 100).toFixed(2)}%`,
          },
        ]
      : []),
  ]

  return (
    <Panel
      title="What changed this week"
      sub="Last 7 days vs your typical week (28-day ÷ 4)"
    >
      <p className="mb-4 text-sm text-muted">
        These are estimates based on your channel analytics — not a guarantee of future
        performance. Comparisons are vs your 28-day average week, not a specific prior week.
      </p>
      <DiffTable rows={rows} />
    </Panel>
  )
}

// ── Per-performer static "why" narrative ─────────────────────────────────────

// Derive a one-line "why" from the backend-computed score components.
// Components are 0–100 where 50 = channel average.
// This is static copy derived from the existing payload — no extra LLM call.
function deriveWhyNarrative(
  kind: 'top' | 'bottom',
  components: { retention: number | null; engagement: number | null; views: number | null } | null | undefined,
): string {
  if (!components) {
    return kind === 'top'
      ? 'Outperformed your channel average across measured signals.'
      : 'Underperformed vs your channel average across measured signals.'
  }

  const { retention, engagement, views } = components
  const signals: string[] = []

  if (retention != null) {
    if (retention >= 65) signals.push('strong watch-through')
    else if (retention <= 35) signals.push('lower watch-through than usual')
  }
  if (engagement != null) {
    if (engagement >= 65) signals.push('above-average likes + comments')
    else if (engagement <= 35) signals.push('below-average engagement')
  }
  if (views != null) {
    if (views >= 65) signals.push('above-average reach')
    else if (views <= 35) signals.push('below-average reach')
  }

  if (signals.length === 0) {
    return kind === 'top'
      ? 'Near-average on all measured signals — consistent with your DNA baseline.'
      : 'Near-average on all measured signals — no single factor stands out.'
  }

  if (kind === 'top') {
    return `Drove ${signals.join(' and ')} relative to your channel average.`
  }
  return `Showed ${signals.join(' and ')} relative to your channel average.`
}

// ── Page-level framing ───────────────────────────────────────────────────────

interface InsightsFramingProps {
  data: InsightsResponse
}

export function InsightsFraming({ data }: InsightsFramingProps) {
  const { top_performers, bottom_performers, dna } = data

  const hasDna = dna.version != null
  const topCount = top_performers.length
  const bottomCount = bottom_performers.length

  return (
    <Panel
      title="What this is showing — and why it matters"
      sub="Your channel, grounded in your own data"
    >
      <div className="space-y-4 text-sm leading-relaxed text-muted">
        <p>
          <span className="font-medium text-fg">Channel snapshot</span> shows how many
          videos AutoClip has ingested and processed from your channel. Higher ingested
          counts mean your DNA profile is based on more of your own content — which makes
          clip scoring more accurate.
        </p>

        {hasDna ? (
          <p>
            <span className="font-medium text-fg">Your DNA</span> (v{dna.version},{' '}
            {dna.status}) is the model of your channel style. It was learned from{' '}
            {topCount > 0 ? `your top ${topCount} performer${topCount !== 1 ? 's' : ''}` : 'your top videos'}
            {bottomCount > 0
              ? ` and contrasted with ${bottomCount} underperformer${bottomCount !== 1 ? 's' : ''}`
              : ''}
            . Every clip score is ranked against this DNA — not against a generic
            virality benchmark.
          </p>
        ) : (
          <p>
            <span className="font-medium text-fg">Your DNA</span> hasn't been built yet.
            Once you sync your channel and run DNA build, this page will show which videos
            drove your style and why — grounded in your actual analytics, not generic advice.
          </p>
        )}

        {topCount > 0 && (
          <p>
            <span className="font-medium text-fg">Top performers</span> below are the
            videos that outperformed your channel average on retention, engagement, and
            reach — the three signals AutoClip uses to score clips. Each row shows a
            one-line "why" derived from your own metrics.
          </p>
        )}

        {bottomCount > 0 && (
          <p>
            <span className="font-medium text-fg">Underperformers</span> are the contrast
            set — useful for understanding which content patterns didn't resonate with your
            audience. AutoClip uses both lists to sharpen clip selection.
          </p>
        )}

        <p>
          <span className="font-medium text-fg">What changed this week</span> compares
          your last 7 days to your 28-day average. It tells you whether your channel
          velocity is accelerating or cooling down — not what went viral.
        </p>

        <p className="text-xs text-subtle">
          All figures are estimates derived from your YouTube Analytics data. They do not
          predict future performance or promise any outcome.
        </p>
      </div>
    </Panel>
  )
}

// ── Per-row why pill ─────────────────────────────────────────────────────────

// Exported so PerformerPanel can import it without knowing the full framing.
export { deriveWhyNarrative }
