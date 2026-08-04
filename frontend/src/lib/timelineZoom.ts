// Timeline zoom + scroll arithmetic (Issue 390).
//
// All pure functions. The timeline components own DOM and state; this owns the
// maths, so the zoom model is testable without a layout engine — which matters,
// because jsdom has none and every one of these values is a function of a
// measured pixel width.
//
// THE MODEL: `pxPerSecond` (pps) plus a horizontal `scrollLeft` in CSS pixels.
// Content width is `duration * pps`. Everything else derives from those two.
// Audacity's vocabulary is the reference — Fit to Width, zoom in/out as
// doubling/halving, and zoom anchored on the pointer.

/** Zoom step per keyboard press / wheel notch. Audacity's Ctrl+1/Ctrl+3 double and halve. */
export const ZOOM_FACTOR = 2

/**
 * Never zoom out past fit — there is nothing to see left of 0 or right of the
 * end, and allowing it produces dead space the user has to scroll back out of.
 */
export const MIN_PX_PER_SECOND = 1 / 60 // one pixel per minute, for very long sources

/**
 * Upper bound. At 4000 px/s one pixel is 0.25 ms, far past both the peaks
 * resolution (Issue 392 stores ~31 pairs/second) and any editing need — past
 * this you are zooming into interpolation, not data.
 */
export const MAX_PX_PER_SECOND = 4000

/** Pixels per second that fits `duration` exactly into `viewportWidth`. */
export function fitPxPerSecond(viewportWidth: number, duration: number): number {
  if (!(viewportWidth > 0) || !(duration > 0)) return MIN_PX_PER_SECOND
  return viewportWidth / duration
}

/**
 * Clamp a candidate zoom.
 *
 * The lower bound is `fit`, not MIN_PX_PER_SECOND: zooming out past fit is
 * always wrong. MIN_PX_PER_SECOND only guards the degenerate case where fit
 * itself is unmeasurably small.
 */
export function clampPxPerSecond(
  pps: number,
  { viewportWidth, duration }: { viewportWidth: number; duration: number },
): number {
  const fit = fitPxPerSecond(viewportWidth, duration)
  const lower = Math.max(MIN_PX_PER_SECOND, Math.min(fit, MAX_PX_PER_SECOND))
  if (!Number.isFinite(pps) || pps <= 0) return lower
  return Math.min(MAX_PX_PER_SECOND, Math.max(lower, pps))
}

/** Largest scrollLeft that keeps content filling the viewport. */
export function maxScrollLeft({
  duration,
  pxPerSecond,
  viewportWidth,
}: {
  duration: number
  pxPerSecond: number
  viewportWidth: number
}): number {
  return Math.max(0, duration * pxPerSecond - viewportWidth)
}

export function clampScrollLeft(
  scrollLeft: number,
  args: { duration: number; pxPerSecond: number; viewportWidth: number },
): number {
  if (!Number.isFinite(scrollLeft)) return 0
  return Math.min(maxScrollLeft(args), Math.max(0, scrollLeft))
}

export interface Viewport {
  duration: number
  pxPerSecond: number
  scrollLeft: number
  viewportWidth: number
}

/** Seconds → x offset in viewport pixels (0 = left edge of the visible area). */
export function timeToX(time: number, v: Viewport): number {
  return time * v.pxPerSecond - v.scrollLeft
}

/** Viewport-relative x → seconds, clamped to the clip. */
export function xToTime(x: number, v: Viewport): number {
  if (!(v.pxPerSecond > 0)) return 0
  return Math.min(v.duration, Math.max(0, (x + v.scrollLeft) / v.pxPerSecond))
}

/**
 * Zoom while keeping the instant under `anchorX` pinned to that same pixel.
 *
 * This is what makes zoom feel like a magnifier rather than a jump: Audacity
 * anchors on the pointer, and without it every zoom step throws away the user's
 * place and they have to re-find it.
 *
 * Returns the new pps AND the scrollLeft that preserves the anchor, because the
 * two must change together — applying one without the other is the bug.
 */
export function zoomAtAnchor(
  v: Viewport,
  nextPxPerSecondRaw: number,
  anchorX: number,
): { pxPerSecond: number; scrollLeft: number } {
  const pxPerSecond = clampPxPerSecond(nextPxPerSecondRaw, v)
  // The instant currently under the anchor pixel.
  const anchorTime = xToTime(anchorX, v)
  // Solve for the scroll that puts anchorTime back under anchorX at the new zoom.
  const scrollLeft = clampScrollLeft(anchorTime * pxPerSecond - anchorX, {
    duration: v.duration,
    pxPerSecond,
    viewportWidth: v.viewportWidth,
  })
  return { pxPerSecond, scrollLeft }
}

/**
 * Scroll so `time` is visible, nudging by the minimum needed.
 *
 * Deliberately NOT "centre the playhead": re-centring on every frame makes the
 * waveform slide continuously under a stationary playhead, which is disorienting
 * and is why most editors only scroll when the playhead would otherwise leave.
 * `margin` keeps it off the very edge so you can see what is coming.
 */
export function scrollToKeepVisible(
  time: number,
  v: Viewport,
  { margin = 24 }: { margin?: number } = {},
): number {
  const x = timeToX(time, v)
  const limit = Math.min(margin, v.viewportWidth / 4)
  let next = v.scrollLeft
  if (x < limit) next = time * v.pxPerSecond - limit
  else if (x > v.viewportWidth - limit) next = time * v.pxPerSecond - (v.viewportWidth - limit)
  return clampScrollLeft(next, v)
}

// Human-readable tick intervals, in seconds. A ruler must land on values a
// person reads as round — 1s, 5s, 30s, 2m — never on 1.37s, which is what a
// naive `duration / n` produces. Extends by decade past a minute.
const NICE_INTERVALS = [
  0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200,
]

/**
 * Smallest "nice" interval whose ticks are at least `minSpacingPx` apart.
 *
 * This is what makes the ruler adaptive: at any zoom, labels stay legible and
 * never collide, because spacing is the constraint and the interval is chosen to
 * satisfy it — rather than a fixed tick count that crowds as you zoom out.
 */
export function niceTickInterval(pxPerSecond: number, minSpacingPx = 80): number {
  if (!(pxPerSecond > 0)) return NICE_INTERVALS[NICE_INTERVALS.length - 1]
  for (const interval of NICE_INTERVALS) {
    if (interval * pxPerSecond >= minSpacingPx) return interval
  }
  // Past the table (an extremely long source zoomed all the way out): keep
  // doubling the largest entry so the ruler degrades instead of crowding.
  let interval = NICE_INTERVALS[NICE_INTERVALS.length - 1]
  while (interval * pxPerSecond < minSpacingPx) interval *= 2
  return interval
}

/** Tick times visible in the current viewport, inclusive of partial edges. */
export function visibleTicks(v: Viewport, interval: number): number[] {
  if (!(interval > 0) || !(v.pxPerSecond > 0)) return []
  const firstTime = Math.max(0, Math.floor(xToTime(0, v) / interval) * interval)
  const lastTime = Math.min(v.duration, xToTime(v.viewportWidth, v))
  const ticks: number[] = []
  // Guard against a pathological interval producing an unbounded loop.
  const maxTicks = Math.ceil(v.viewportWidth / 8) + 2
  for (let t = firstTime; t <= lastTime + 1e-9 && ticks.length < maxTicks; t += interval) {
    // Re-derive from the index rather than accumulating, so float error cannot
    // drift the labels at high zoom.
    ticks.push(Number((Math.round(t / interval) * interval).toFixed(6)))
  }
  return ticks
}

/** `m:ss` for short spans, `h:mm:ss` past an hour; sub-second at high zoom. */
export function formatTick(seconds: number, interval: number): string {
  const total = Math.max(0, seconds)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  if (interval < 1) {
    const fixed = s.toFixed(1).padStart(4, '0')
    return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${fixed}` : `${m}:${fixed}`
  }
  const ss = String(Math.floor(s)).padStart(2, '0')
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`
}
