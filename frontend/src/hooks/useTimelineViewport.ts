import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import {
  clampScrollLeft,
  fitPxPerSecond,
  scrollToKeepVisible,
  timeToX,
  xToTime,
  ZOOM_FACTOR,
  zoomAtAnchor,
  type Viewport,
} from '@/lib/timelineZoom'

export interface TimelineViewport extends Viewport {
  /** Attach to the element whose width defines the visible timeline. */
  ref: React.RefObject<HTMLDivElement | null>
  /** True while the user has zoomed in past fit — enables the "reset" affordance. */
  isZoomed: boolean
  zoomIn: (anchorX?: number) => void
  zoomOut: (anchorX?: number) => void
  zoomToFit: () => void
  scrollBy: (dx: number) => void
  /** Seconds → x within the viewport. */
  timeToX: (t: number) => number
  /** clientX (page coords) → seconds. */
  clientXToTime: (clientX: number) => number
}

/**
 * Zoom + horizontal scroll state for a timeline (Issue 390).
 *
 * MEASUREMENT. Width is read with `getBoundingClientRect` in a LAYOUT effect and
 * `ResizeObserver` is only the change-notifier. That ordering is deliberate: a
 * ResizeObserver has not fired yet on first paint, so an implementation that
 * waits for it renders one frame at width 0 — and at width 0 every derived value
 * (fit zoom, tick interval, playhead x) is 0 or NaN.
 *
 * WHEEL. The listener is attached with `addEventListener(..., { passive: false })`
 * rather than React's `onWheel`, because React attaches wheel listeners passively
 * and `preventDefault()` inside one silently does nothing (facebook/react#14856).
 * Without preventDefault, Ctrl+wheel zooms the whole browser page instead of the
 * timeline. Trackpad pinch arrives as a wheel event with `ctrlKey: true` (MDN), so
 * handling that one case gives pinch-to-zoom for free.
 */
export function useTimelineViewport({
  duration,
  followTime,
}: {
  duration: number
  /** Playhead, in seconds. The viewport scrolls to keep it visible during playback. */
  followTime?: number
}): TimelineViewport {
  const ref = useRef<HTMLDivElement>(null)
  const [viewportWidth, setViewportWidth] = useState(0)
  // `null` means "follow fit" — so a resize keeps a never-zoomed timeline fitted
  // instead of freezing it at the width it first measured.
  const [pxPerSecondState, setPxPerSecondState] = useState<number | null>(null)
  const [scrollLeft, setScrollLeft] = useState(0)

  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const measure = () => {
      const w = el.getBoundingClientRect().width
      setViewportWidth((prev) => (Math.abs(prev - w) < 0.5 ? prev : w))
    }
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const fit = fitPxPerSecond(viewportWidth, duration)
  const pxPerSecond = pxPerSecondState ?? fit
  const v: Viewport = { duration, pxPerSecond, scrollLeft, viewportWidth }

  // Keep scroll legal when duration, zoom or width changes underneath us.
  useEffect(() => {
    setScrollLeft((prev) => clampScrollLeft(prev, v))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [duration, pxPerSecond, viewportWidth])

  const applyZoom = useCallback(
    (factor: number, anchorX?: number) => {
      const el = ref.current
      if (!el) return
      const width = el.getBoundingClientRect().width
      const current: Viewport = {
        duration,
        pxPerSecond: pxPerSecondState ?? fitPxPerSecond(width, duration),
        scrollLeft,
        viewportWidth: width,
      }
      // Default anchor is the viewport centre — the sensible target for a
      // keyboard or button zoom, where there is no pointer to anchor on.
      const anchor = anchorX ?? width / 2
      const next = zoomAtAnchor(current, current.pxPerSecond * factor, anchor)
      setPxPerSecondState(next.pxPerSecond)
      setScrollLeft(next.scrollLeft)
    },
    [duration, pxPerSecondState, scrollLeft],
  )

  const zoomIn = useCallback((anchorX?: number) => applyZoom(ZOOM_FACTOR, anchorX), [applyZoom])
  const zoomOut = useCallback((anchorX?: number) => applyZoom(1 / ZOOM_FACTOR, anchorX), [applyZoom])

  const zoomToFit = useCallback(() => {
    // Back to `null`, not to the computed fit value: that re-arms "follow fit"
    // so a later resize stays fitted.
    setPxPerSecondState(null)
    setScrollLeft(0)
  }, [])

  const scrollByPx = useCallback(
    (dx: number) => {
      const el = ref.current
      if (!el) return
      const width = el.getBoundingClientRect().width
      setScrollLeft((prev) =>
        clampScrollLeft(prev + dx, {
          duration,
          pxPerSecond: pxPerSecondState ?? fitPxPerSecond(width, duration),
          viewportWidth: width,
        }),
      )
    },
    [duration, pxPerSecondState],
  )

  // Wheel: plain = scroll, Ctrl/Cmd = zoom. See the docstring for why this is not
  // React's onWheel.
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      const rect = el.getBoundingClientRect()
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault()
        // deltaY is inverted relative to zoom: wheel-up (negative) zooms in.
        applyZoom(e.deltaY < 0 ? ZOOM_FACTOR : 1 / ZOOM_FACTOR, e.clientX - rect.left)
        return
      }
      // A horizontal-capable device (trackpad, tilt wheel) reports deltaX; a
      // plain vertical wheel is mapped to horizontal scroll, because a timeline
      // has no vertical axis to give it to.
      const dx = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY
      if (dx === 0) return
      // Only swallow the gesture when there is somewhere to scroll, so a fitted
      // timeline does not trap the page's scroll.
      const pps = pxPerSecondState ?? fitPxPerSecond(rect.width, duration)
      if (duration * pps - rect.width <= 0) return
      e.preventDefault()
      scrollByPx(dx)
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [applyZoom, scrollByPx, duration, pxPerSecondState])

  // Follow the playhead, but only when it would otherwise leave the viewport.
  useEffect(() => {
    if (followTime === undefined || viewportWidth <= 0) return
    setScrollLeft((prev) => scrollToKeepVisible(followTime, { ...v, scrollLeft: prev }))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [followTime, pxPerSecond, viewportWidth, duration])

  return {
    ...v,
    ref,
    isZoomed: pxPerSecondState !== null && pxPerSecondState > fit * 1.001,
    zoomIn,
    zoomOut,
    zoomToFit,
    scrollBy: scrollByPx,
    timeToX: (t: number) => timeToX(t, v),
    clientXToTime: (clientX: number) => {
      const el = ref.current
      if (!el) return 0
      return xToTime(clientX - el.getBoundingClientRect().left, v)
    },
  }
}
