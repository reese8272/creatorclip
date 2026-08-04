import type { EditDocumentCut, EditorCut, TranscriptWord } from '@/types'

// The editor's cut list — identity, merging, and edge adjustment (Issue 390).
//
// Extracted from ShortFormEditor because two of the three defects below were
// only possible while this logic lived inside a component and could not be
// tested in isolation.

/** Cuts closer than this are merged into one on commit. */
export const ADJACENT_MERGE_S = 0.05

/** Minimum selectable cut duration — guards accidental zero-length clicks. */
export const MIN_CUT_S = 0.1

let idCounter = 0

/**
 * A stable, client-generated identity for a cut.
 *
 * Cuts used to be addressed by array index — as React keys, as the argument to
 * `removeCut`, and in `aria-label`. `mergeAdjacent` re-sorts by `start_s`, so
 * dragging one cut's edge past a neighbour renumbered every cut mid-gesture:
 * the drag detached and, with pointer capture held, the user carried on dragging
 * a DIFFERENT cut. An id is what makes an edge drag (and later #391's undo
 * stack) able to name the thing it is acting on.
 */
export function newCutId(): string {
  // randomUUID is unavailable on http:// origins in some browsers and in older
  // Safari; the counter keeps ids unique within the session, which is all the
  // client needs.
  const uuid = globalThis.crypto?.randomUUID?.()
  return uuid ?? `cut_${Date.now().toString(36)}_${(idCounter++).toString(36)}`
}

/**
 * Coerce a value parsed from storage into a usable cut, or null if it cannot be.
 *
 * Every persisted cut written before this issue lacks an `id`. Without
 * backfilling one they would all key as `undefined`, and React would collapse
 * the entire list into a single node.
 */
export function normalizeCut(value: unknown): EditorCut | null {
  if (typeof value !== 'object' || value === null) return null
  const raw = value as Partial<EditorCut>
  const start_s = Number(raw.start_s)
  const end_s = Number(raw.end_s)
  if (!Number.isFinite(start_s) || !Number.isFinite(end_s)) return null
  if (end_s <= start_s || start_s < 0) return null

  const indices =
    Array.isArray(raw.indices) &&
    raw.indices.length === 2 &&
    raw.indices.every((n) => Number.isInteger(n) && n >= 0)
      ? ([raw.indices[0], raw.indices[1]] as [number, number])
      : undefined

  return {
    id: typeof raw.id === 'string' && raw.id ? raw.id : newCutId(),
    start_s,
    end_s,
    ...(indices ? { indices } : {}),
  }
}

/**
 * Sort by start time and merge cuts that touch, WITHOUT mutating the input.
 *
 * The previous implementation copied with `.slice()` — which is shallow — and
 * then wrote `last.end_s = …` straight through to the caller's objects. Because
 * the editor did `setUndo(cuts)` immediately before
 * `setCuts(mergeAdjacent([...cuts, newCut]))`, the "undo snapshot" was mutated by
 * the very merge it was meant to protect against. **Today's single-level undo is
 * already wrong for any merged cut** — this is the fix, and it is also the
 * precondition for #391's command stack, where every history entry would
 * otherwise alias the same objects and undo would become a no-op.
 *
 * The survivor keeps the EARLIER cut's id, so a drag in progress keeps naming
 * something that still exists.
 */
export function mergeAdjacent(cuts: readonly EditorCut[]): EditorCut[] {
  const sorted = [...cuts].sort((a, b) => a.start_s - b.start_s)
  const out: EditorCut[] = []
  for (const cur of sorted) {
    const last = out[out.length - 1]
    if (last && cur.start_s <= last.end_s + ADJACENT_MERGE_S) {
      out[out.length - 1] = {
        ...last,
        end_s: Math.max(last.end_s, cur.end_s),
        ...mergedIndices(last, cur),
      }
    } else {
      out.push({ ...cur })
    }
  }
  return out
}

/** Union of two word spans — absent when neither cut has one. */
function mergedIndices(a: EditorCut, b: EditorCut): { indices?: [number, number] } {
  if (!a.indices && !b.indices) return {}
  if (!a.indices) return { indices: b.indices }
  if (!b.indices) return { indices: a.indices }
  return {
    indices: [Math.min(a.indices[0], b.indices[0]), Math.max(a.indices[1], b.indices[1])],
  }
}

export function removeCutById(cuts: readonly EditorCut[], id: string): EditorCut[] {
  return cuts.filter((c) => c.id !== id)
}

/**
 * Move one edge of a cut, clamped to the clip and to `MIN_CUT_S`.
 *
 * Returns the list un-merged: merging happens once, on gesture completion, so a
 * neighbour cannot be absorbed (and the dragged id retired) mid-drag.
 */
export function adjustCutEdge(
  cuts: readonly EditorCut[],
  id: string,
  side: 'start' | 'end',
  time: number,
  duration: number,
): EditorCut[] {
  return cuts.map((c) => {
    if (c.id !== id) return c
    if (side === 'start') {
      const start_s = Math.max(0, Math.min(time, c.end_s - MIN_CUT_S))
      return { ...c, start_s }
    }
    const end_s = Math.min(duration, Math.max(time, c.start_s + MIN_CUT_S))
    return { ...c, end_s }
  })
}

/**
 * The word span a time range covers, or null when it covers none.
 *
 * Overlap, not enclosure: clipping one millisecond of a word pulls the whole
 * word in. That is a deliberate snap-outward policy — a cut that removed half a
 * word would leave an audible fragment.
 */
export function timeRangeToIndices(
  words: readonly TranscriptWord[],
  startS: number,
  endS: number,
): [number, number] | null {
  let first = -1
  let last = -1
  for (let i = 0; i < words.length; i++) {
    const w = words[i]
    if (w.start_s <= endS && w.end_s >= startS) {
      if (first === -1) first = i
      last = i
    }
  }
  return first === -1 ? null : [first, last]
}

/**
 * Build a cut for a dragged time range.
 *
 * `indices` is OMITTED when no word overlaps, rather than defaulting to `[0, 0]`
 * as the old code did — that fallback silently struck through the first word of
 * the transcript and labelled the cut with it, on every drag over a gap.
 */
export function cutFromRange(
  words: readonly TranscriptWord[],
  start_s: number,
  end_s: number,
): EditorCut {
  const indices = timeRangeToIndices(words, start_s, end_s)
  return { id: newCutId(), start_s, end_s, ...(indices ? { indices } : {}) }
}

/**
 * Strip a cut list down to what is persisted (Issue 391).
 *
 * `indices` is DERIVED and never stored: it is a pure function of (times,
 * transcript), and the transcript is server-owned and mutable, so persisting it
 * would create a second source of truth that goes stale silently and can index
 * past the end of `words`. The inverse is `withIndices`.
 */
export function toDocumentCuts(cuts: readonly EditorCut[]): EditDocumentCut[] {
  return cuts.map(({ id, start_s, end_s }) => ({ id, start_s, end_s }))
}

/**
 * Recompute the transcript word span for each stored cut.
 *
 * Safe only because Issue 390 made committed times land on snapped word
 * boundaries; before that, recomputation could pick a neighbouring word.
 */
export function withIndices(
  cuts: readonly EditDocumentCut[],
  words: readonly TranscriptWord[],
): EditorCut[] {
  return cuts.map((c) => {
    const indices = timeRangeToIndices(words, c.start_s, c.end_s)
    return { ...c, ...(indices ? { indices } : {}) }
  })
}

/** Word indices covered by any cut — drives the transcript strikethrough. */
export function cutWordIndices(cuts: readonly EditorCut[]): Set<number> {
  const out = new Set<number>()
  for (const c of cuts) {
    if (!c.indices) continue
    for (let i = c.indices[0]; i <= c.indices[1]; i++) out.add(i)
  }
  return out
}
