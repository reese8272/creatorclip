import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Badge } from '@/components/ui/badge'
import type { DnaProfile } from '@/types'

function SummaryCard({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="rounded-md border border-default bg-surface p-5 shadow-sm shadow-inset">
      <div className="mb-2 text-label uppercase tracking-[0.08em] text-muted">{label}</div>
      <div className="font-mono text-xl font-semibold leading-tight text-fg">{value}</div>
      {sub && <div className="mt-2 text-xs text-subtle">{sub}</div>}
    </div>
  )
}

// Three-up overview: DNA status/version, video count, clips rendered.
// `dna` is null when no profile has been built yet.
export function SummaryCards({
  dna,
  videoCount,
  clipsRendered,
}: {
  dna: DnaProfile | null
  videoCount: number
  clipsRendered: number
}) {
  return (
    <div className="mb-8 grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-3">
      <SummaryCard
        label="Creator DNA"
        value={dna ? <Badge variant="accent">{dna.status}</Badge> : 'Not built'}
        sub={dna ? `v${dna.version}` : null}
      />
      <SummaryCard label="Videos" value={videoCount} sub="in library" />
      <SummaryCard
        label="Clips rendered"
        value={clipsRendered}
        sub={
          <Link to="/review" className="text-accent-text hover:text-fg">
            Review queue →
          </Link>
        }
      />
    </div>
  )
}
