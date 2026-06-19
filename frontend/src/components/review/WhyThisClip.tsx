import { FitBadge } from '@/components/ui/fit-badge'
import { fitTier } from '@/lib/fit'
import type { ReviewClip } from '@/types'

// Issue 94 transparency: the named principle + Claude's reasoning + score/timing
// the engine cited. The honest fit tier leads; the raw score stays below as the
// transparency detail (fit estimate, not a promise).
export function WhyThisClip({ clip }: { clip: ReviewClip }) {
  const setupStart = clip.setup_start_s ?? clip.start_s
  return (
    <div className="text-sm">
      <div className="mb-3 flex items-center justify-between gap-3 border-b border-default pb-2">
        <span className="font-mono text-xs text-accent">[principle] {clip.principle || '—'}</span>
        <FitBadge tier={fitTier(clip.score)} />
      </div>
      <div className="leading-relaxed text-fg">
        {clip.reasoning ||
          'No reasoning recorded for this clip. The scoring engine still ranked it — the explanation is just not on file.'}
      </div>
      <div className="mt-3 flex justify-between border-t border-default pt-3 font-mono text-xs text-subtle">
        <span>Score (fit estimate, not a guarantee)</span>
        <strong className="text-fg">{clip.score != null ? clip.score.toFixed(2) : '—'}</strong>
      </div>
      <div className="flex justify-between font-mono text-xs text-subtle">
        <span>Setup → peak → end</span>
        <strong className="text-fg">
          {setupStart.toFixed(1)}s → {(clip.peak_s ?? clip.start_s).toFixed(1)}s →{' '}
          {clip.end_s.toFixed(1)}s
        </strong>
      </div>
    </div>
  )
}
