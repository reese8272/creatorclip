import { timeToX, type Viewport } from '@/lib/timelineZoom'
import type { EditorCut } from '@/types'

// Pointer hit-testing and snapping for the timeline (Issue 390).
//
// Pure functions, in `lib/` rather than beside the components, so the whole
// interaction model is testable without a layout engine — and so the "is this a
// canvas?" gate over `components/editor/` never has to reason about it.
//
// EVERY THRESHOLD HERE IS IN PIXELS, converted to seconds at the point of use.
// That is the entire trick behind "feels the same at every zoom": an 8px snap
// radius is ~10 seconds of tolerance on a 22-minute source viewed at fit, and
// ~4 milliseconds at 64x. Storing either of those as a duration would make
// snapping unusable at one end or the other.

/** How close the pointer must be to a cut edge to grab it. */
export const EDGE_GRAB_PX = 6

/** How close a dragged boundary must come to a candidate to snap to it. */
export const SNAP_THRESHOLD_PX = 8

export type TimelineHit =
  | { kind: 'edge'; cutId: string; side: 'start' | 'end' }
  | { kind: 'body'; cutId: string }
  | { kind: 'empty' }

/**
 * What is under `x` (viewport-relative pixels)?
 *
 * Precedence is edge > body > empty, and it is not arbitrary: the edges live
 * INSIDE the body, so testing body first would make every edge ungrabbable.
 * Cuts are scanned in reverse so the last-painted (topmost) wins a tie.
 */
export function hitTest(
  x: number,
  {
    cuts,
    viewport,
    edgeGrabPx = EDGE_GRAB_PX,
  }: { cuts: readonly EditorCut[]; viewport: Viewport; edgeGrabPx?: number },
): TimelineHit {
  let best: { hit: TimelineHit; distance: number } | null = null

  for (let i = cuts.length - 1; i >= 0; i--) {
    const cut = cuts[i]
    const startX = timeToX(cut.start_s, viewport)
    const endX = timeToX(cut.end_s, viewport)
    for (const [side, edgeX] of [
      ['start', startX],
      ['end', endX],
    ] as const) {
      const distance = Math.abs(edgeX - x)
      if (distance > edgeGrabPx) continue
      if (!best || distance < best.distance) {
        best = { hit: { kind: 'edge', cutId: cut.id, side }, distance }
      }
    }
  }
  if (best) return best.hit

  for (let i = cuts.length - 1; i >= 0; i--) {
    const cut = cuts[i]
    if (x >= timeToX(cut.start_s, viewport) && x <= timeToX(cut.end_s, viewport)) {
      return { kind: 'body', cutId: cut.id }
    }
  }
  return { kind: 'empty' }
}

/**
 * Every instant a dragged boundary may snap to, sorted ascending.
 *
 * Word boundaries are the ones that matter — cutting mid-word leaves an audible
 * fragment — but clip edges, the playhead and other cuts are all things a
 * creator lines up against, which is the set Kdenlive snaps to as well.
 */
export function buildSnapCandidates({
  wordBoundaries = [],
  cuts = [],
  playhead,
  duration,
  excludeCutId,
}: {
  wordBoundaries?: readonly number[]
  cuts?: readonly EditorCut[]
  playhead?: number
  duration: number
  /** The cut being dragged — its own edges must not attract themselves. */
  excludeCutId?: string
}): number[] {
  const out: number[] = [0, duration, ...wordBoundaries]
  if (playhead !== undefined) out.push(playhead)
  for (const c of cuts) {
    if (c.id === excludeCutId) continue
    out.push(c.start_s, c.end_s)
  }
  return [...new Set(out.filter((t) => Number.isFinite(t) && t >= 0 && t <= duration))].sort(
    (a, b) => a - b,
  )
}

export interface SnapResult {
  time: number
  snapped: boolean
  /** The candidate landed on, when snapped — for the indicator and aria text. */
  candidate?: number
}

/**
 * Snap `time` to the nearest candidate within the pixel threshold.
 *
 * `candidates` must be sorted; a binary search keeps this cheap enough to run on
 * every pointermove even with a full transcript's worth of word boundaries.
 */
export function snapTime(
  time: number,
  candidates: readonly number[],
  {
    pxPerSecond,
    thresholdPx = SNAP_THRESHOLD_PX,
    enabled = true,
  }: { pxPerSecond: number; thresholdPx?: number; enabled?: boolean },
): SnapResult {
  if (!enabled || candidates.length === 0 || !(pxPerSecond > 0)) return { time, snapped: false }

  const thresholdS = thresholdPx / pxPerSecond
  let lo = 0
  let hi = candidates.length
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (candidates[mid] < time) lo = mid + 1
    else hi = mid
  }

  // Only the two neighbours of the insertion point can be nearest.
  let bestCandidate: number | undefined
  let bestDistance = Number.POSITIVE_INFINITY
  for (const idx of [lo - 1, lo]) {
    const candidate = candidates[idx]
    if (candidate === undefined) continue
    const distance = Math.abs(candidate - time)
    if (distance < bestDistance) {
      bestDistance = distance
      bestCandidate = candidate
    }
  }

  if (bestCandidate === undefined || bestDistance > thresholdS) return { time, snapped: false }
  return { time: bestCandidate, snapped: true, candidate: bestCandidate }
}

/**
 * Word start and end times, flattened and sorted.
 *
 * Both edges are candidates: a cut usually starts at the beginning of a word and
 * ends at the end of one, and offering only one edge makes half of all cuts
 * impossible to place exactly.
 */
export function wordBoundaries(
  words: readonly { start_s: number; end_s: number }[],
): number[] {
  const out: number[] = []
  for (const w of words) out.push(w.start_s, w.end_s)
  return out.sort((a, b) => a - b)
}

/** The word a snapped boundary landed on — for "snapped to “world”". */
export function wordAtBoundary(
  words: readonly { word: string; start_s: number; end_s: number }[],
  time: number,
  epsilon = 1e-6,
): string | null {
  for (const w of words) {
    if (Math.abs(w.start_s - time) <= epsilon || Math.abs(w.end_s - time) <= epsilon) return w.word
  }
  return null
}
