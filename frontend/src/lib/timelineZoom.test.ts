import { describe, expect, it } from 'vitest'
import {
  clampPxPerSecond,
  fitPxPerSecond,
  formatTick,
  MAX_PX_PER_SECOND,
  maxScrollLeft,
  niceTickInterval,
  scrollToKeepVisible,
  timeToX,
  visibleTicks,
  xToTime,
  zoomAtAnchor,
  type Viewport,
} from './timelineZoom'

// A 40s clip in a 1100px viewport, zoomed to fit: 27.5 px/s.
function fitted(overrides: Partial<Viewport> = {}): Viewport {
  const duration = 40
  const viewportWidth = 1100
  return {
    duration,
    viewportWidth,
    pxPerSecond: fitPxPerSecond(viewportWidth, duration),
    scrollLeft: 0,
    ...overrides,
  }
}

describe('fit + clamp', () => {
  it('fit puts the whole clip in the viewport exactly', () => {
    const v = fitted()
    expect(v.pxPerSecond).toBeCloseTo(27.5, 6)
    expect(maxScrollLeft(v)).toBe(0)
    expect(timeToX(v.duration, v)).toBeCloseTo(v.viewportWidth, 6)
  })

  it('refuses to zoom out past fit — there is nothing out there to see', () => {
    const v = fitted()
    expect(clampPxPerSecond(v.pxPerSecond / 4, v)).toBeCloseTo(v.pxPerSecond, 6)
  })

  it('clamps at the upper bound and survives garbage input', () => {
    const v = fitted()
    expect(clampPxPerSecond(1e9, v)).toBe(MAX_PX_PER_SECOND)
    expect(clampPxPerSecond(Number.NaN, v)).toBeCloseTo(v.pxPerSecond, 6)
    expect(clampPxPerSecond(0, v)).toBeCloseTo(v.pxPerSecond, 6)
    expect(clampPxPerSecond(-5, v)).toBeCloseTo(v.pxPerSecond, 6)
  })

  it('a zero-width viewport (first paint, display:none) does not divide by zero', () => {
    expect(fitPxPerSecond(0, 40)).toBeGreaterThan(0)
    expect(Number.isFinite(fitPxPerSecond(0, 40))).toBe(true)
    expect(Number.isFinite(fitPxPerSecond(1100, 0))).toBe(true)
  })
})

describe('time ↔ x', () => {
  it('round-trips through the current scroll and zoom', () => {
    const v = fitted({ pxPerSecond: 110, scrollLeft: 550 })
    for (const t of [0, 5, 12.5, 39.9]) {
      expect(xToTime(timeToX(t, v), v)).toBeCloseTo(t, 6)
    }
  })

  it('clamps to the clip rather than returning out-of-range times', () => {
    const v = fitted({ pxPerSecond: 110, scrollLeft: 0 })
    expect(xToTime(-500, v)).toBe(0)
    expect(xToTime(99999, v)).toBe(v.duration)
  })
})

describe('zoomAtAnchor', () => {
  it('pins the instant under the anchor pixel — the magnifier property', () => {
    const v = fitted({ pxPerSecond: 110, scrollLeft: 550 })
    const anchorX = 300
    const before = xToTime(anchorX, v)

    const next = zoomAtAnchor(v, v.pxPerSecond * 2, anchorX)
    const after = xToTime(anchorX, { ...v, ...next })
    expect(after).toBeCloseTo(before, 6)
  })

  it('holds the anchor when zooming OUT as well', () => {
    const v = fitted({ pxPerSecond: 440, scrollLeft: 2000 })
    const anchorX = 700
    const before = xToTime(anchorX, v)
    const next = zoomAtAnchor(v, v.pxPerSecond / 2, anchorX)
    expect(xToTime(anchorX, { ...v, ...next })).toBeCloseTo(before, 6)
  })

  it('cannot leave scrollLeft outside its legal range', () => {
    const v = fitted({ pxPerSecond: 440, scrollLeft: 0 })
    // Anchor at the far left while zooming out hard would push scroll negative.
    const next = zoomAtAnchor(v, 1, 0)
    expect(next.scrollLeft).toBeGreaterThanOrEqual(0)
    expect(next.scrollLeft).toBeLessThanOrEqual(
      maxScrollLeft({ ...v, pxPerSecond: next.pxPerSecond }),
    )
  })

  it('zoom-to-fit converges in ONE step and stays there', () => {
    // The convergence property the plan called out: if any ancestor let content
    // widen the box, fit would iterate forever. Fit is a fixed point.
    const v = fitted({ pxPerSecond: 880, scrollLeft: 3000 })
    const fit = fitPxPerSecond(v.viewportWidth, v.duration)
    const once = zoomAtAnchor(v, fit, 0)
    expect(once.pxPerSecond).toBeCloseTo(fit, 6)
    expect(once.scrollLeft).toBe(0)

    const twice = zoomAtAnchor({ ...v, ...once }, fit, 0)
    expect(twice).toEqual(once)
  })
})

describe('scrollToKeepVisible', () => {
  it('does not move while the playhead is comfortably inside the viewport', () => {
    const v = fitted({ pxPerSecond: 110, scrollLeft: 0 })
    // t=5 → x=550, dead centre of 1100.
    expect(scrollToKeepVisible(5, v)).toBe(v.scrollLeft)
  })

  it('nudges by the minimum when the playhead runs off the right edge', () => {
    const v = fitted({ pxPerSecond: 110, scrollLeft: 0 })
    const t = 12 // x = 1320, past the 1100 viewport
    const next = scrollToKeepVisible(t, v)
    expect(next).toBeGreaterThan(0)
    const x = timeToX(t, { ...v, scrollLeft: next })
    // Now visible, and near the right edge — not re-centred.
    expect(x).toBeLessThanOrEqual(v.viewportWidth)
    expect(x).toBeGreaterThan(v.viewportWidth / 2)
  })

  it('scrolls back when the playhead is left of the viewport', () => {
    const v = fitted({ pxPerSecond: 110, scrollLeft: 2000 })
    const next = scrollToKeepVisible(1, v)
    expect(next).toBeLessThan(2000)
    expect(timeToX(1, { ...v, scrollLeft: next })).toBeGreaterThanOrEqual(0)
  })

  it('never scrolls outside the legal range at the very start or end', () => {
    const v = fitted({ pxPerSecond: 110, scrollLeft: 0 })
    expect(scrollToKeepVisible(0, v)).toBe(0)
    expect(scrollToKeepVisible(v.duration, v)).toBeLessThanOrEqual(maxScrollLeft(v))
  })
})

describe('niceTickInterval', () => {
  it('only ever returns values a human reads as round', () => {
    const allowed = new Set([0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200])
    for (const pps of [0.05, 0.5, 2, 7, 27.5, 110, 440, 2000]) {
      const interval = niceTickInterval(pps)
      expect(allowed.has(interval) || interval % 7200 === 0).toBe(true)
    }
  })

  it('guarantees the requested minimum spacing at every zoom', () => {
    for (const pps of [0.02, 0.5, 2, 7, 27.5, 110, 440, 2000, MAX_PX_PER_SECOND]) {
      expect(niceTickInterval(pps, 80) * pps).toBeGreaterThanOrEqual(80)
    }
  })

  it('gets finer as you zoom in — the adaptive property', () => {
    expect(niceTickInterval(2)).toBeGreaterThan(niceTickInterval(200))
  })

  it('degrades instead of hanging on an absurdly long source', () => {
    // 24h at fit in 1100px ≈ 0.0127 px/s — past the end of the table.
    const interval = niceTickInterval(1100 / 86400)
    expect(Number.isFinite(interval)).toBe(true)
    expect(interval * (1100 / 86400)).toBeGreaterThanOrEqual(80)
  })
})

describe('visibleTicks', () => {
  it('covers the viewport and nothing beyond the clip', () => {
    const v = fitted({ pxPerSecond: 110, scrollLeft: 0 })
    const ticks = visibleTicks(v, 1)
    expect(ticks[0]).toBe(0)
    expect(Math.max(...ticks)).toBeLessThanOrEqual(v.duration)
    // 1100px at 110px/s = 10s of content, so ~11 ticks at 1s.
    expect(ticks.length).toBeGreaterThanOrEqual(10)
    expect(ticks.length).toBeLessThanOrEqual(12)
  })

  it('starts at the first tick at or before the left edge when scrolled', () => {
    const v = fitted({ pxPerSecond: 110, scrollLeft: 550 }) // left edge = 5s
    const ticks = visibleTicks(v, 1)
    expect(ticks[0]).toBe(5)
  })

  it('does not accumulate float drift at high zoom', () => {
    const v = fitted({ duration: 40, pxPerSecond: 2000, scrollLeft: 0 })
    const ticks = visibleTicks(v, 0.1)
    for (const t of ticks) {
      expect(Math.abs(t * 10 - Math.round(t * 10))).toBeLessThan(1e-6)
    }
  })

  it('is bounded even if handed a degenerate interval', () => {
    const v = fitted({ pxPerSecond: 110 })
    expect(visibleTicks(v, 0)).toEqual([])
    expect(visibleTicks(v, 1e-9).length).toBeLessThan(200)
  })
})

describe('formatTick', () => {
  it('shows sub-second precision only when the interval needs it', () => {
    expect(formatTick(5.5, 1)).toBe('0:05')
    expect(formatTick(5.5, 0.5)).toBe('0:05.5')
  })

  it('adds hours past an hour', () => {
    expect(formatTick(3725, 60)).toBe('1:02:05')
    expect(formatTick(65, 1)).toBe('1:05')
  })
})
