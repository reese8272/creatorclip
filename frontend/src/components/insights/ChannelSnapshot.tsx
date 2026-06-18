import { Cell, gridCls, Panel } from '@/components/insights/InsightsPanel'
import type { ChannelTotals, DnaStats } from '@/types'

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

export function DnaSnapshot({ dna }: { dna: DnaStats }) {
  const version = dna.version != null ? `v${dna.version} · ${dna.status}` : 'Not built yet'
  return (
    <Panel title="Your DNA at a glance" sub={version}>
      <div className={gridCls}>
        <Cell
          label="Optimal clip"
          value={dna.optimal_clip_len_s != null ? `${dna.optimal_clip_len_s.toFixed(0)}s` : '—'}
        />
        <Cell
          label="Best region"
          value={dna.best_source_region ? dna.best_source_region.replace('_', ' ') : '—'}
        />
        <Cell
          label="Upload gap"
          value={dna.optimal_upload_gap_h != null ? `${dna.optimal_upload_gap_h.toFixed(1)}h` : '—'}
        />
      </div>
    </Panel>
  )
}
