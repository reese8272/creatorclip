import { formatTick, niceTickInterval, timeToX, visibleTicks, type Viewport } from '@/lib/timelineZoom'
import { cn } from '@/lib/utils'

/**
 * TimelineRuler — adaptive time ruler (Issue 390).
 *
 * Replaces three fixed labels (start / midpoint / end) that told a creator
 * nothing about where they were once the timeline could zoom.
 *
 * DOM, NOT CANVAS — deliberately, and enforced. `src/test/no-synthetic-waveform`
 * asserts that `getContext('2d')` appears in EXACTLY one file under
 * `components/editor/` (Waveform.tsx); relaxing that assertion to `toContain` is
 * how the fabricated waveform Issue 392 removed would find its way back. DOM is
 * also cheap here by construction: `niceTickInterval` guarantees ≥80px between
 * labels, so a 1100px rail emits at most ~14 nodes at any zoom level.
 *
 * The tick INTERVAL is chosen from a table of human-round values (1s, 5s, 30s,
 * 2m…) constrained by minimum label spacing — never `duration / n`, which
 * produces unreadable values like 1.37s and crowds as you zoom out.
 */
export function TimelineRuler({
  viewport,
  className,
  minSpacingPx = 80,
}: {
  viewport: Viewport
  className?: string
  /** Minimum px between labels. Larger for a cramped rail. */
  minSpacingPx?: number
}) {
  const interval = niceTickInterval(viewport.pxPerSecond, minSpacingPx)
  const ticks = visibleTicks(viewport, interval)

  return (
    // aria-hidden: the ruler is a visual scale for the waveform beside it. The
    // playhead thumb announces the actual position via aria-valuetext, so a
    // screen reader reading a dozen timecodes would be noise, not information.
    <div
      aria-hidden="true"
      className={cn('pointer-events-none relative h-4 select-none', className)}
    >
      {ticks.map((t) => (
        <span
          key={t}
          className="absolute top-0 font-mono text-label text-subtle"
          style={{
            // `left` is derived from TIME, never from the map index — the
            // no-synthetic-waveform gate is watching for index arithmetic here.
            left: `${timeToX(t, viewport)}px`,
            // The first label would otherwise be half-clipped by the rail's left
            // edge; centring every other one keeps ticks readable at the marks.
            transform: t === 0 ? 'translateX(0)' : 'translateX(-50%)',
          }}
        >
          {formatTick(t, interval)}
        </span>
      ))}
    </div>
  )
}
