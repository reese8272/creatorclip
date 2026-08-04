import { useCallback, useRef, useState, type ReactNode } from 'react'
import { TimelineRuler } from '@/components/editor/TimelineRuler'
import { Button } from '@/components/ui/button'
import { Maximize, ZoomIn, ZoomOut } from '@/components/ui/icon'
import { ICON_SIZE } from '@/components/ui/iconSizes'
import { useTimelineViewport, type TimelineViewport } from '@/hooks/useTimelineViewport'
import { formatTick } from '@/lib/timelineZoom'
import { hitTest, type TimelineHit } from '@/lib/timelineInteraction'
import { cn } from '@/lib/utils'
import type { EditorCut } from '@/types'

/** How far a pointer may move and still count as a click rather than a drag. */
const CLICK_SLOP_PX = 4

export interface RailDrag {
  hit: TimelineHit
  /** Where the gesture began, in seconds. */
  fromTime: number
  /** Where the pointer is now, in seconds. */
  toTime: number
  /** The live viewport, so handlers can convert px thresholds without a ref. */
  viewport: TimelineViewport
}

export interface TimelineRailProps {
  duration: number
  currentTime: number
  cuts: readonly EditorCut[]
  /** Accessible name for the rail as a whole, e.g. "Clip timeline". */
  label: string
  /** Height of the interactive lane in px. The ruler sits above it. */
  laneHeight?: number
  /**
   * The element the drag handlers and the width measurement both attach to.
   * Callers pass their existing test id so a rect stub keeps finding it.
   */
  railTestId?: string
  /** Painted inside the lane, positioned by the caller using `viewport`. */
  children?: (viewport: TimelineViewport) => ReactNode
  /** Live drag, for a preview overlay. Called on every pointermove. */
  onDrag?: (drag: RailDrag) => void
  /** Gesture completed past the click slop. */
  onDragEnd?: (drag: RailDrag) => void
  /** A press that never became a drag. */
  onClick?: (hit: TimelineHit, time: number) => void
  className?: string
}

/**
 * TimelineRail — the shared zoom, scroll and pointer surface (Issue 390).
 *
 * Both the short-form clip timeline and the long-form source timeline compose
 * this. They previously carried two independent copies of `xToTime`, two mouse
 * drag implementations and two fixed three-label rulers, which is how they
 * drifted apart in the first place.
 *
 * POINTER EVENTS, NOT MOUSE. Touch and pen produce nothing from `onMouseDown`,
 * so half the input devices did nothing at all. `setPointerCapture` also removes
 * the `onMouseLeave` commit hack the old code needed: a drag that leaves the
 * element keeps delivering events instead of being abandoned mid-gesture.
 *
 * THE MEASURED ELEMENT, THE POINTER TARGET AND `railTestId` ARE THE SAME NODE.
 * jsdom has no layout, so tests stub one element's rect; putting a wrapper
 * between them would leave the measured node reporting width 0, where every
 * derived value is 0 or NaN.
 *
 * ARIA: this is a `group`, not a `slider`. `role="slider"` forces every
 * descendant to `presentation` (MDN), which is why the waveform's `role="img"`
 * and the per-cut labels were being swallowed while the container claimed to be
 * one. The playhead is the slider; cut edges become sliders too, following the
 * W3C APG multi-thumb pattern.
 */
export function TimelineRail({
  duration,
  currentTime,
  cuts,
  label,
  laneHeight = 80,
  railTestId,
  children,
  onDrag,
  onDragEnd,
  onClick,
  className,
}: TimelineRailProps) {
  const railRef = useRef<HTMLDivElement>(null)
  const viewport = useTimelineViewport({ duration, followTime: currentTime, containerRef: railRef })
  const { clientXToTime, timeToX, zoomIn, zoomOut, zoomToFit, isZoomed } = viewport

  // The drag lives in a ref, never in `hasPointerCapture`: jsdom stubs that to
  // return false, so any handler gated on it is dead in tests — which is why the
  // player's own scrub drag has never been testable.
  const dragRef = useRef<{
    pointerId: number
    hit: TimelineHit
    fromTime: number
    startX: number
    moved: boolean
  } | null>(null)
  const [cursor, setCursor] = useState<'crosshair' | 'ew-resize'>('crosshair')
  // Set when a gesture completes as a DRAG. Interactive children (the long-form
  // timeline paints a <button> per candidate clip over the waveform) would
  // otherwise receive the trailing click and open a clip the user was only
  // dragging across. A ref plus onClickCapture works regardless of z-order or
  // DOM position — and, unlike relying on real pointer-capture retargeting, it
  // also works in jsdom, where capture is a no-op stub.
  const suppressClickRef = useRef(false)

  const hitAt = useCallback(
    (clientX: number): TimelineHit => {
      const el = railRef.current
      if (!el) return { kind: 'empty' }
      const x = clientX - el.getBoundingClientRect().left
      return hitTest(x, { cuts, viewport })
    },
    [cuts, viewport],
  )

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return
    const hit = hitAt(e.clientX)
    const fromTime = clientXToTime(e.clientX)
    dragRef.current = { pointerId: e.pointerId, hit, fromTime, startX: e.clientX, moved: false }
    // Guarded: jsdom has no real capture, and a pointerId can be absent.
    if (Number.isFinite(e.pointerId)) e.currentTarget.setPointerCapture?.(e.pointerId)
  }

  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current
    if (!drag) {
      // Hover affordance only — tells the creator an edge is grabbable before
      // they commit to the gesture.
      const kind = hitAt(e.clientX).kind
      setCursor(kind === 'edge' ? 'ew-resize' : 'crosshair')
      return
    }
    if (!drag.moved && Math.abs(e.clientX - drag.startX) > CLICK_SLOP_PX) drag.moved = true
    if (!drag.moved) return
    onDrag?.({ hit: drag.hit, fromTime: drag.fromTime, toTime: clientXToTime(e.clientX), viewport })
  }

  const endDrag = (e: React.PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current
    if (!drag) return
    dragRef.current = null
    const toTime = clientXToTime(e.clientX)
    if (drag.moved) {
      suppressClickRef.current = true
      onDragEnd?.({ hit: drag.hit, fromTime: drag.fromTime, toTime, viewport })
    } else {
      onClick?.(drag.hit, drag.fromTime)
    }
  }

  const railStyle = { height: laneHeight, cursor }

  return (
    <div className={cn('flex flex-col gap-1', className)}>
      <div className="flex shrink-0 items-center justify-end gap-1">
        <Button variant="ghost" size="sm" aria-label="Zoom out" onClick={() => zoomOut()}>
          <ZoomOut className={ICON_SIZE.sm} aria-hidden="true" />
        </Button>
        <Button
          variant="ghost"
          size="sm"
          aria-label="Zoom to fit"
          disabled={!isZoomed}
          onClick={zoomToFit}
        >
          <Maximize className={ICON_SIZE.sm} aria-hidden="true" />
        </Button>
        <Button variant="ghost" size="sm" aria-label="Zoom in" onClick={() => zoomIn()}>
          <ZoomIn className={ICON_SIZE.sm} aria-hidden="true" />
        </Button>
      </div>

      <TimelineRuler viewport={viewport} />

      <div
        ref={railRef}
        data-testid={railTestId}
        data-timeline-rail
        role="group"
        aria-label={label}
        // touch-none: without it a touch drag scrolls the page instead of
        // scrubbing, which is the whole point of adopting pointer events.
        className="relative select-none overflow-hidden rounded-md border border-default bg-surface touch-none"
        style={railStyle}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onClickCapture={(e) => {
          if (!suppressClickRef.current) return
          suppressClickRef.current = false
          e.stopPropagation()
          e.preventDefault()
        }}
        onLostPointerCapture={() => {
          dragRef.current = null
        }}
      >
        {children?.(viewport)}

        {/* The playhead — a real slider, and the only one until cut edges land.
            It carries the accessible name the timeline used to claim for its
            container, so existing queries keep working and now point at
            something genuinely operable. */}
        <div
          role="slider"
          tabIndex={0}
          aria-label="Timeline scrubber"
          aria-valuemin={0}
          aria-valuemax={Math.max(0, duration)}
          aria-valuenow={currentTime}
          aria-valuetext={`${formatTick(currentTime, 1)} of ${formatTick(duration, 1)}`}
          className="pointer-events-none absolute top-0 bottom-0 w-px bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-border"
          style={{ left: `${timeToX(currentTime)}px` }}
        />
      </div>
    </div>
  )
}
