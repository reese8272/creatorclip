import { Cell, gridCls, Panel } from '@/components/insights/InsightsPanel'
import type { ChannelTotals } from '@/types'

// "Your DNA at a glance" moved to ChannelFingerprint (Issue 379) — the bare
// three-stat DnaSnapshot card was replaced by a designed artifact with a
// shareable-card tier. See components/insights/ChannelFingerprint.tsx.

const fmt = (n: number | null | undefined) => (n == null ? '—' : n.toLocaleString())

export function ChannelSnapshot({ totals }: { totals: ChannelTotals }) {
  return (
    <Panel title="Channel snapshot" sub="Last refresh from your YouTube analytics">
      <div className={gridCls}>
        <Cell label="Videos analysed" value={fmt(totals.videos_analyzed)} />
        <Cell label="Long-form" value={fmt(totals.longs)} />
        <Cell label="Shorts" value={fmt(totals.shorts)} />
        <Cell label="Ingested" value={fmt(totals.ingested_done)} />
        <Cell label="Minutes processed" value={fmt(totals.total_minutes_processed)} />
      </div>
    </Panel>
  )
}
