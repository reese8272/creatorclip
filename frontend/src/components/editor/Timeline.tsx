import { useState } from 'react'
import { TimelineRail, type RailDrag } from '@/components/editor/TimelineRail'
import { Waveform } from '@/components/editor/Waveform'
import type { TimelineViewport } from '@/hooks/useTimelineViewport'
import { adjustCutEdge, MIN_CUT_S } from '@/lib/editorCuts'
import {
  buildSnapCandidates,
  snapTime,
  wordAtBoundary,
  wordBoundaries,
} from '@/lib/timelineInteraction'
import { formatTick } from '@/lib/timelineZoom'
import { cn } from '@/lib/utils'
import type { WaveformPeaks } from '@/lib/peaks'
import type { EditorCut, TranscriptWord } from '@/types'

export interface Cut {
  start_s: number
  end_s: number
}

interface TimelineProps {
  /** Total duration of the clip in seconds. */
  duration: number
  /** Current playhead position in seconds. */
  currentTime: number
  /** Queued cuts, drawn as regions with draggable edges. */
  cuts: readonly EditorCut[]
  /** Emitted when the user clicks the rail to seek. */
  onSeek: (time: number) => void
  /** Emitted when a new time-range selection is completed. */
  onSelection: (cut: Cut) => void
  /** Emitted when an existing cut's edge is dragged to a new time. */
  onCutsChange?: (cuts: EditorCut[]) => void
  /** Transcript words — the snap targets that make cuts land on word edges. */
  words?: readonly TranscriptWord[]
  /** Whether snapping is active. Owned by the caller so it can be persisted. */
  snapEnabled?: boolean
  /** Real audio peaks for the PARENT SOURCE, windowed to this clip (Issue 392). */
  peaks?: WaveformPeaks | null
  /** Clip start within the source — the window offset into `peaks`. */
  sourceStartS?: number
  className?: string
}

/**
 * Timeline — the short-form clip editing surface.
 *
 * The zoom, scroll, pointer and ruler machinery lives in `TimelineRail`, which
 * the long-form source timeline shares. This owns only what is specific to a
 * clip: windowing the source waveform to the clip's span, snapping to transcript
 * word boundaries, and rendering cut regions with draggable edges.
 *
 * Positions are PIXELS from the viewport, not percentages of the duration.
 * Percentages cannot express a scrolled, zoomed view — they only ever describe
 * the whole clip.
 */
export function Timeline({
  duration,
  currentTime,
  cuts,
  onSeek,
  onSelection,
  onCutsChange,
  words = [],
  snapEnabled = true,
  peaks = null,
  sourceStartS = 0,
  className,
}: TimelineProps) {
  // Live gesture preview. Cuts are only merged on completion, so a neighbour
  // cannot be absorbed — and the dragged id retired — mid-drag.
  const [preview, setPreview] = useState<{
    range?: { from: number; to: number }
    cuts?: EditorCut[]
    snappedTo?: number
    snappedWord?: string | null
  } | null>(null)

  const shownCuts = preview?.cuts ?? cuts

  function candidatesFor(excludeCutId?: string) {
    return buildSnapCandidates({
      wordBoundaries: wordBoundaries(words),
      cuts,
      playhead: currentTime,
      duration,
      excludeCutId,
    })
  }

  function applySnap(time: number, viewport: TimelineViewport, excludeCutId?: string) {
    const result = snapTime(time, candidatesFor(excludeCutId), {
      pxPerSecond: viewport.pxPerSecond,
      enabled: snapEnabled,
    })
    return {
      time: result.time,
      snappedTo: result.snapped ? result.candidate : undefined,
      snappedWord: result.snapped ? wordAtBoundary(words, result.time) : null,
    }
  }

  function handleDrag(drag: RailDrag, viewport: TimelineViewport) {
    if (drag.hit.kind === 'edge') {
      const { cutId, side } = drag.hit
      const snap = applySnap(drag.toTime, viewport, cutId)
      setPreview({
        cuts: adjustCutEdge(cuts, cutId, side, snap.time, duration),
        snappedTo: snap.snappedTo,
        snappedWord: snap.snappedWord,
      })
      return
    }
    if (drag.hit.kind === 'empty') {
      const snap = applySnap(drag.toTime, viewport)
      setPreview({
        range: { from: drag.fromTime, to: snap.time },
        snappedTo: snap.snappedTo,
        snappedWord: snap.snappedWord,
      })
    }
  }

  function handleDragEnd(drag: RailDrag, viewport: TimelineViewport) {
    setPreview(null)
    if (drag.hit.kind === 'edge') {
      const { cutId, side } = drag.hit
      const snap = applySnap(drag.toTime, viewport, cutId)
      onCutsChange?.(adjustCutEdge(cuts, cutId, side, snap.time, duration))
      return
    }
    if (drag.hit.kind !== 'empty') return
    const snapStart = applySnap(drag.fromTime, viewport)
    const snapEnd = applySnap(drag.toTime, viewport)
    const lo = Math.min(snapStart.time, snapEnd.time)
    const hi = Math.max(snapStart.time, snapEnd.time)
    if (hi - lo >= MIN_CUT_S) onSelection({ start_s: lo, end_s: hi })
  }

  return (
    <TimelineRail
      className={className}
      duration={duration}
      currentTime={currentTime}
      cuts={cuts}
      label="Clip timeline"
      onDrag={(d) => handleDrag(d, d.viewport)}
      onDragEnd={(d) => handleDragEnd(d, d.viewport)}
      onClick={(hit, time) => {
        if (hit.kind === 'empty') onSeek(time)
      }}
    >
      {(viewport) => (
          <>
            <div className="pointer-events-none absolute inset-0 text-accent">
              <Waveform
                peaks={peaks}
                startS={sourceStartS}
                endS={sourceStartS + duration}
                ariaLabel={peaks ? 'Clip audio waveform' : 'Waveform unavailable'}
              />
            </div>

            {shownCuts.map((c, i) => (
              <CutRegion
                key={c.id}
                cut={c}
                ordinal={i + 1}
                duration={duration}
                viewport={viewport}
                snappedTo={preview?.snappedTo}
              />
            ))}

            {preview?.range && (
              <div
                aria-hidden="true"
                className="pointer-events-none absolute inset-y-0 bg-accent opacity-30"
                style={{
                  left: `${viewport.timeToX(Math.min(preview.range.from, preview.range.to))}px`,
                  width: `${Math.abs(
                    viewport.timeToX(preview.range.to) - viewport.timeToX(preview.range.from),
                  )}px`,
                }}
              />
            )}

            {preview?.snappedTo !== undefined && (
              <SnapIndicator
                time={preview.snappedTo}
                word={preview.snappedWord}
                viewport={viewport}
              />
            )}
          </>
      )}
    </TimelineRail>
  )
}

/** One cut, with a focusable thumb on each edge (W3C APG multi-thumb). */
function CutRegion({
  cut,
  ordinal,
  duration,
  viewport,
  snappedTo,
}: {
  cut: EditorCut
  ordinal: number
  duration: number
  viewport: TimelineViewport
  snappedTo?: number
}) {
  const left = viewport.timeToX(cut.start_s)
  const right = viewport.timeToX(cut.end_s)
  const label = `Cut ${ordinal}: ${cut.start_s.toFixed(2)}s–${cut.end_s.toFixed(2)}s`

  return (
    <div role="group" aria-label={label}>
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-y-0 bg-danger opacity-25"
        style={{ left: `${left}px`, width: `${Math.max(1, right - left)}px` }}
      />
      {(['start', 'end'] as const).map((side) => {
        const time = side === 'start' ? cut.start_s : cut.end_s
        return (
          <div
            key={side}
            role="slider"
            tabIndex={0}
            aria-label={`Cut ${ordinal} ${side}`}
            aria-valuemin={0}
            aria-valuemax={duration}
            aria-valuenow={time}
            aria-valuetext={formatTick(time, 0.5)}
            data-cut-edge={`${cut.id}:${side}`}
            data-snapped={snappedTo !== undefined && Math.abs(snappedTo - time) < 1e-6 ? '' : undefined}
            className={cn(
              'absolute inset-y-0 w-[3px] cursor-ew-resize bg-danger',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-border',
              'data-snapped:bg-accent',
            )}
            style={{ left: `${(side === 'start' ? left : right) - 1}px` }}
          />
        )
      })}
    </div>
  )
}

/** The line a dragged boundary will land on, plus the word it belongs to. */
function SnapIndicator({
  time,
  word,
  viewport,
}: {
  time: number
  word?: string | null
  viewport: TimelineViewport
}) {
  return (
    <div
      aria-hidden="true"
      className="pointer-events-none absolute inset-y-0 z-10 w-px bg-accent"
      style={{ left: `${viewport.timeToX(time)}px` }}
    >
      <span className="absolute -top-0.5 left-1 whitespace-nowrap rounded-sm bg-elevated px-1 font-mono text-label text-accent-text shadow-sm">
        {formatTick(time, 0.5)}
        {word ? ` · ${word}` : ''}
      </span>
    </div>
  )
}
