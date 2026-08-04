import { describe, expect, it } from 'vitest'
import {
  buildSnapCandidates,
  EDGE_GRAB_PX,
  hitTest,
  SNAP_THRESHOLD_PX,
  snapTime,
  wordAtBoundary,
  wordBoundaries,
} from './timelineInteraction'
import { fitPxPerSecond, timeToX, type Viewport } from './timelineZoom'
import type { EditorCut } from '@/types'

const DURATION = 40
const WIDTH = 1100

function viewport(over: Partial<Viewport> = {}): Viewport {
  return {
    duration: DURATION,
    viewportWidth: WIDTH,
    pxPerSecond: fitPxPerSecond(WIDTH, DURATION), // 27.5
    scrollLeft: 0,
    ...over,
  }
}

const CUTS: EditorCut[] = [
  { id: 'a', start_s: 10, end_s: 20 },
  { id: 'b', start_s: 30, end_s: 35 },
]

describe('hitTest', () => {
  it('grabs an edge, and reports which one', () => {
    const v = viewport()
    expect(hitTest(timeToX(10, v), { cuts: CUTS, viewport: v })).toEqual({
      kind: 'edge',
      cutId: 'a',
      side: 'start',
    })
    expect(hitTest(timeToX(20, v), { cuts: CUTS, viewport: v })).toEqual({
      kind: 'edge',
      cutId: 'a',
      side: 'end',
    })
  })

  it('prefers an edge over the body it lives inside', () => {
    // Testing body first would make every edge ungrabbable — the edges are IN
    // the body by definition.
    const v = viewport()
    const justInside = timeToX(10, v) + 2
    expect(hitTest(justInside, { cuts: CUTS, viewport: v }).kind).toBe('edge')
  })

  it('falls back to the body away from the edges, then to empty', () => {
    const v = viewport()
    expect(hitTest(timeToX(15, v), { cuts: CUTS, viewport: v })).toEqual({ kind: 'body', cutId: 'a' })
    expect(hitTest(timeToX(25, v), { cuts: CUTS, viewport: v })).toEqual({ kind: 'empty' })
  })

  it('picks the NEAREST edge when two are within reach', () => {
    const v = viewport({ pxPerSecond: 1 }) // 1px per second — edges crowd together
    const tight: EditorCut[] = [
      { id: 'x', start_s: 10, end_s: 14 },
      { id: 'y', start_s: 15, end_s: 20 },
    ]
    // x=14.4s is 0.4px from x.end and 0.6px from y.start.
    const hit = hitTest(14.4, { cuts: tight, viewport: v })
    expect(hit).toEqual({ kind: 'edge', cutId: 'x', side: 'end' })
  })

  it('THE ZOOM PROPERTY: the same pixel grab zone spans different SECONDS', () => {
    // This is what makes an edge feel equally grabbable at every zoom. At fit,
    // 6px is a fifth of a second; at 64x it is a few milliseconds.
    const fit = viewport()
    const zoomed = viewport({ pxPerSecond: fit.pxPerSecond * 64 })

    const fitSeconds = EDGE_GRAB_PX / fit.pxPerSecond
    const zoomedSeconds = EDGE_GRAB_PX / zoomed.pxPerSecond
    expect(fitSeconds / zoomedSeconds).toBeCloseTo(64, 6)

    // And it really does grab at both: half a grab-zone off the edge, either way.
    for (const v of [fit, zoomed]) {
      expect(hitTest(timeToX(10, v) + EDGE_GRAB_PX / 2, { cuts: CUTS, viewport: v }).kind).toBe('edge')
      expect(hitTest(timeToX(10, v) + EDGE_GRAB_PX * 2, { cuts: CUTS, viewport: v }).kind).not.toBe(
        'edge',
      )
    }
  })

  it('accounts for scroll', () => {
    const v = viewport({ pxPerSecond: 110, scrollLeft: 550 }) // left edge = 5s
    // Cut 'a' starts at 10s → x = 10*110 - 550 = 550.
    expect(hitTest(550, { cuts: CUTS, viewport: v })).toEqual({
      kind: 'edge',
      cutId: 'a',
      side: 'start',
    })
  })

  it('is empty when there are no cuts', () => {
    expect(hitTest(100, { cuts: [], viewport: viewport() })).toEqual({ kind: 'empty' })
  })
})

describe('buildSnapCandidates', () => {
  it('includes clip edges, words, other cuts and the playhead — sorted and unique', () => {
    const out = buildSnapCandidates({
      wordBoundaries: [5, 5, 12],
      cuts: CUTS,
      playhead: 8,
      duration: DURATION,
    })
    expect(out).toEqual([0, 5, 8, 10, 12, 20, 30, 35, 40])
  })

  it('excludes the cut being dragged, so its edges cannot attract themselves', () => {
    const out = buildSnapCandidates({ cuts: CUTS, duration: DURATION, excludeCutId: 'a' })
    expect(out).not.toContain(10)
    expect(out).not.toContain(20)
    expect(out).toContain(30)
  })

  it('drops candidates outside the clip', () => {
    const out = buildSnapCandidates({ wordBoundaries: [-5, 999, Number.NaN], duration: DURATION })
    expect(out).toEqual([0, 40])
  })
})

describe('snapTime', () => {
  const candidates = [0, 10, 20, 40]

  it('snaps to the nearest candidate inside the threshold', () => {
    const v = viewport() // 27.5 px/s → 8px ≈ 0.29s
    const result = snapTime(10.2, candidates, { pxPerSecond: v.pxPerSecond })
    expect(result).toEqual({ time: 10, snapped: true, candidate: 10 })
  })

  it('leaves the time alone outside the threshold', () => {
    const v = viewport()
    expect(snapTime(15, candidates, { pxPerSecond: v.pxPerSecond })).toEqual({
      time: 15,
      snapped: false,
    })
  })

  it('THE ZOOM PROPERTY: the threshold is constant in PIXELS, not seconds', () => {
    const fit = fitPxPerSecond(WIDTH, DURATION) // 27.5 px/s
    const zoomed = fit * 64

    // 0.2s away: comfortably inside 8px at fit (~0.29s of tolerance)…
    expect(snapTime(10.2, candidates, { pxPerSecond: fit }).snapped).toBe(true)
    // …and far outside it once zoomed in, where 8px is ~4.5ms.
    expect(snapTime(10.2, candidates, { pxPerSecond: zoomed }).snapped).toBe(false)
    // At 64x you have to be genuinely close.
    expect(snapTime(10.002, candidates, { pxPerSecond: zoomed }).snapped).toBe(true)

    // Stated as the invariant it is:
    for (const pps of [fit, fit * 4, fit * 64]) {
      const justInside = 10 + (SNAP_THRESHOLD_PX * 0.9) / pps
      const justOutside = 10 + (SNAP_THRESHOLD_PX * 1.1) / pps
      expect(snapTime(justInside, candidates, { pxPerSecond: pps }).snapped).toBe(true)
      expect(snapTime(justOutside, candidates, { pxPerSecond: pps }).snapped).toBe(false)
    }
  })

  it('is a no-op when snapping is switched off', () => {
    // The toggle must be honoured here, not by the caller skipping the call —
    // otherwise "off" and "nothing nearby" take different code paths.
    expect(snapTime(10.05, candidates, { pxPerSecond: 27.5, enabled: false })).toEqual({
      time: 10.05,
      snapped: false,
    })
  })

  it('survives an empty candidate list and a degenerate zoom', () => {
    expect(snapTime(5, [], { pxPerSecond: 27.5 }).snapped).toBe(false)
    expect(snapTime(5, candidates, { pxPerSecond: 0 }).snapped).toBe(false)
  })

  it('picks the nearer of two neighbours straddling the time', () => {
    const wide = [10, 11]
    expect(snapTime(10.6, wide, { pxPerSecond: 5 }).candidate).toBe(11)
    expect(snapTime(10.4, wide, { pxPerSecond: 5 }).candidate).toBe(10)
  })

  it('snaps at the very ends of the clip', () => {
    expect(snapTime(0.05, candidates, { pxPerSecond: 27.5 }).candidate).toBe(0)
    expect(snapTime(39.95, candidates, { pxPerSecond: 27.5 }).candidate).toBe(40)
  })
})

describe('wordBoundaries / wordAtBoundary', () => {
  const words = [
    { word: 'Hello', start_s: 0, end_s: 1 },
    { word: 'world', start_s: 1.2, end_s: 2 },
  ]

  it('offers both edges of every word, sorted', () => {
    expect(wordBoundaries(words)).toEqual([0, 1, 1.2, 2])
  })

  it('names the word a snapped boundary landed on', () => {
    expect(wordAtBoundary(words, 2)).toBe('world')
    expect(wordAtBoundary(words, 0)).toBe('Hello')
    expect(wordAtBoundary(words, 1.6)).toBeNull()
  })
})
