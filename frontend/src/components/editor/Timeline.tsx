import { useRef, useState } from 'react'
import { Waveform } from '@/components/editor/Waveform'
import type { WaveformPeaks } from '@/lib/peaks'
import { cn } from '@/lib/utils'

// The visual height of the waveform canvas in px (CSS pixels).
const WAVE_HEIGHT = 80

// Minimum selectable cut duration (prevents accidental zero-length clicks).
const MIN_CUT_S = 0.1

export interface Cut {
  start_s: number
  end_s: number
}

interface TimelineProps {
  /** Total duration of the clip in seconds. */
  duration: number
  /** Current playhead position in seconds (driven by the <video> timeupdate). */
  currentTime: number
  /** Queued word-editor cuts to overlay on the timeline as dimmed regions. */
  cuts: Cut[]
  /** Emitted when the user clicks or drags on the waveform to seek. */
  onSeek: (time: number) => void
  /** Emitted when a new time-range selection is completed on the waveform. */
  onSelection: (cut: Cut) => void
  /** Real audio peaks for the PARENT SOURCE, or null → an honest flat track.
   *  Issue 392: one artifact per video, windowed here to the clip's span. */
  peaks?: WaveformPeaks | null
  /** Clip start within the source, in seconds — the window offset into `peaks`. */
  sourceStartS?: number
  className?: string
}

/**
 * Timeline — waveform + synced playhead + trim-selection component.
 *
 * Design notes (Issue 188, waveform reworked in 392):
 *  - Industry standard (Descript / Opus / Riverside) is a waveform bar with a
 *    moving playhead and drag-to-select for cuts. HTML5 Canvas + timeupdate is
 *    the idiomatic zero-dependency implementation for this scale.
 *  - ONE waveform path: real server-computed peaks for the parent source,
 *    windowed to this clip's span. There is deliberately no synthetic fallback —
 *    when peaks are absent `Waveform` draws a flat line (Issue 392).
 *  - The playhead is a CSS-positioned div driven by currentTime / duration, re-
 *    rendered only when the ratio changes — no continuous Canvas redraws.
 *  - Drag-to-select emits a Cut on mouseup; it does not mutate the cuts array
 *    (caller owns state). A click without drag seeks instead of selecting.
 */
export function Timeline({
  duration,
  currentTime,
  cuts,
  onSeek,
  onSelection,
  peaks = null,
  sourceStartS = 0,
  className,
}: TimelineProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [dragStart, setDragStart] = useState<number | null>(null)
  const [dragEnd, setDragEnd] = useState<number | null>(null)

  // Ratio: 0..1 fraction into the clip.
  const playRatio = duration > 0 ? Math.min(currentTime / duration, 1) : 0

  // Convert a mouse event's X coordinate to a clip-relative time in seconds.
  function xToTime(clientX: number): number {
    const rect = containerRef.current?.getBoundingClientRect()
    if (!rect || rect.width === 0 || duration <= 0) return 0
    const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width))
    return ratio * duration
  }

  // Mouse interaction — seek on click, select on drag.
  function onMouseDown(e: React.MouseEvent) {
    if (e.button !== 0) return
    setDragStart(xToTime(e.clientX))
    setDragEnd(null)
  }

  function onMouseMove(e: React.MouseEvent) {
    if (dragStart === null) return
    setDragEnd(xToTime(e.clientX))
  }

  function onMouseUp(e: React.MouseEvent) {
    if (dragStart === null) return
    const t = xToTime(e.clientX)
    const lo = Math.min(dragStart, t)
    const hi = Math.max(dragStart, t)

    if (hi - lo < MIN_CUT_S) {
      // Treat as a seek click, not a selection.
      onSeek(dragStart)
    } else {
      onSelection({ start_s: lo, end_s: hi })
    }
    setDragStart(null)
    setDragEnd(null)
  }

  function onMouseLeave() {
    if (dragStart !== null && dragEnd !== null) {
      const lo = Math.min(dragStart, dragEnd)
      const hi = Math.max(dragStart, dragEnd)
      if (hi - lo >= MIN_CUT_S) onSelection({ start_s: lo, end_s: hi })
    }
    setDragStart(null)
    setDragEnd(null)
  }

  // Compute selection overlay geometry (% of container width).
  const selectionStyle =
    dragStart !== null && dragEnd !== null && duration > 0
      ? {
          left: `${(Math.min(dragStart, dragEnd) / duration) * 100}%`,
          width: `${(Math.abs(dragEnd - dragStart) / duration) * 100}%`,
        }
      : null

  return (
    <div
      ref={containerRef}
      className={cn('relative w-full select-none overflow-hidden rounded-md border border-default bg-surface', className)}
      style={{ height: WAVE_HEIGHT + 24 }}
      onMouseDown={onMouseDown}
      onMouseMove={onMouseMove}
      onMouseUp={onMouseUp}
      onMouseLeave={onMouseLeave}
      role="slider"
      aria-label="Timeline scrubber"
      aria-valuemin={0}
      aria-valuemax={duration}
      aria-valuenow={currentTime}
    >
      {/* The creator's ACTUAL audio, windowed from the source's peaks. No
          synthetic fallback exists: absent peaks render a flat line (Issue 392). */}
      <div className="pointer-events-none absolute inset-x-0 top-0 h-[80px] text-accent">
        <Waveform
          peaks={peaks}
          startS={sourceStartS}
          endS={sourceStartS + duration}
          ariaLabel={peaks ? 'Clip audio waveform' : 'Waveform unavailable'}
        />
      </div>

      {/* Cut regions: dimmed overlays for each queued cut. */}
      {duration > 0 &&
        cuts.map((c, i) => (
          <div
            key={i}
            aria-label={`Cut ${i + 1}: ${c.start_s.toFixed(2)}s–${c.end_s.toFixed(2)}s`}
            className="absolute top-0 h-[80px] bg-danger opacity-25"
            style={{
              left: `${(c.start_s / duration) * 100}%`,
              width: `${((c.end_s - c.start_s) / duration) * 100}%`,
              pointerEvents: 'none',
            }}
          />
        ))}

      {/* Active drag selection overlay. */}
      {selectionStyle && (
        <div
          className="absolute top-0 h-[80px] bg-accent opacity-30"
          style={{ ...selectionStyle, pointerEvents: 'none' }}
        />
      )}

      {/* Playhead line + time label. */}
      <div
        aria-hidden="true"
        className="absolute top-0 h-[80px] w-px bg-accent"
        style={{ left: `${playRatio * 100}%`, pointerEvents: 'none' }}
      />

      {/* Time ruler: start / mid / end labels. */}
      <div className="absolute bottom-0 left-0 right-0 flex justify-between px-2 py-1 font-mono text-label text-muted" style={{ pointerEvents: 'none' }}>
        <span>0:00</span>
        {duration > 0 && <span>{fmtTime(duration / 2)}</span>}
        <span>{fmtTime(duration)}</span>
      </div>

      {/* Cursor time tooltip during drag. */}
      {dragStart !== null && dragEnd !== null && (
        <div
          className="pointer-events-none absolute top-1 rounded-sm bg-elevated px-1.5 py-0.5 font-mono text-label text-fg shadow-sm"
          style={{ left: `${(Math.min(dragStart, dragEnd) / Math.max(duration, 1)) * 100}%` }}
        >
          {fmtTime(Math.min(dragStart, dragEnd))} → {fmtTime(Math.max(dragStart, dragEnd))}
        </div>
      )}
    </div>
  )
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function fmtTime(s: number): string {
  const m = Math.floor(s / 60)
  const sec = Math.floor(s % 60)
  return `${m}:${sec.toString().padStart(2, '0')}`
}
