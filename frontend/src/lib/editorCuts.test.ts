import { describe, expect, it } from 'vitest'
import {
  adjustCutEdge,
  cutFromRange,
  cutWordIndices,
  mergeAdjacent,
  MIN_CUT_S,
  newCutId,
  normalizeCut,
  removeCutById,
  timeRangeToIndices,
} from './editorCuts'
import type { EditorCut, TranscriptWord } from '@/types'

const WORDS: TranscriptWord[] = [
  { word: 'Hello', start_s: 0, end_s: 1, index: 0 },
  { word: 'world', start_s: 1, end_s: 2, index: 1 },
  { word: 'again', start_s: 5, end_s: 6, index: 2 },
]

function cut(over: Partial<EditorCut> = {}): EditorCut {
  return { id: newCutId(), start_s: 0, end_s: 1, ...over }
}

describe('mergeAdjacent', () => {
  it('DOES NOT MUTATE its input — the bug that made single-level undo wrong', () => {
    // The old implementation copied with a shallow .slice() and then wrote
    // `last.end_s = …` through to the caller's objects, so the undo snapshot
    // taken immediately before the merge was corrupted by that same merge.
    // Object.freeze makes the old code throw in strict mode; this is red-first.
    const a = Object.freeze(cut({ start_s: 0, end_s: 1 }))
    const b = Object.freeze(cut({ start_s: 1.02, end_s: 2 }))
    const input = Object.freeze([a, b])

    const merged = mergeAdjacent(input)

    expect(merged).toHaveLength(1)
    expect(merged[0].end_s).toBe(2)
    // The originals are untouched — this is the whole point.
    expect(a.end_s).toBe(1)
    expect(b.end_s).toBe(2)
    expect(input).toHaveLength(2)
  })

  it('returns fresh objects even when nothing merges', () => {
    const a = cut({ start_s: 0, end_s: 1 })
    const out = mergeAdjacent([a])
    expect(out[0]).not.toBe(a)
    expect(out[0]).toEqual(a)
  })

  it('keeps the EARLIER cut id so a drag in progress still names something real', () => {
    const first = cut({ id: 'keep-me', start_s: 0, end_s: 1 })
    const second = cut({ id: 'absorbed', start_s: 1.01, end_s: 3 })
    expect(mergeAdjacent([second, first])[0].id).toBe('keep-me')
  })

  it('sorts by start time', () => {
    const out = mergeAdjacent([cut({ start_s: 9, end_s: 10 }), cut({ start_s: 1, end_s: 2 })])
    expect(out.map((c) => c.start_s)).toEqual([1, 9])
  })

  it('leaves a real gap alone', () => {
    expect(mergeAdjacent([cut({ start_s: 0, end_s: 1 }), cut({ start_s: 5, end_s: 6 })])).toHaveLength(2)
  })

  it('unions word spans, and tolerates cuts that have none', () => {
    const withSpan = cut({ start_s: 0, end_s: 1, indices: [0, 1] })
    const alsoSpan = cut({ start_s: 1.01, end_s: 2, indices: [3, 5] })
    expect(mergeAdjacent([withSpan, alsoSpan])[0].indices).toEqual([0, 5])

    const noSpan = cut({ start_s: 1.01, end_s: 2 })
    expect(mergeAdjacent([withSpan, noSpan])[0].indices).toEqual([0, 1])
    expect(mergeAdjacent([cut({ start_s: 0, end_s: 1 }), noSpan])[0].indices).toBeUndefined()
  })

  it('handles an empty list', () => {
    expect(mergeAdjacent([])).toEqual([])
  })
})

describe('normalizeCut', () => {
  it('backfills an id on cuts persisted before ids existed', () => {
    // Without this every legacy cut keys as `undefined` and React collapses the
    // whole list into one node.
    const legacy = normalizeCut({ start_s: 1, end_s: 2, indices: [0, 1] })
    expect(legacy?.id).toEqual(expect.any(String))
    expect(legacy?.id).not.toBe('')
    expect(legacy?.start_s).toBe(1)
    expect(legacy?.indices).toEqual([0, 1])
  })

  it('keeps an id that is already there', () => {
    expect(normalizeCut({ id: 'abc', start_s: 1, end_s: 2 })?.id).toBe('abc')
  })

  it('drops a malformed indices rather than trusting it', () => {
    expect(normalizeCut({ start_s: 1, end_s: 2, indices: [0] })?.indices).toBeUndefined()
    expect(normalizeCut({ start_s: 1, end_s: 2, indices: 'nope' })?.indices).toBeUndefined()
    expect(normalizeCut({ start_s: 1, end_s: 2, indices: [-1, 3] })?.indices).toBeUndefined()
  })

  it('rejects entries that cannot be a cut at all', () => {
    // A single NaN in a legacy blob must not brick the editor.
    expect(normalizeCut(null)).toBeNull()
    expect(normalizeCut('cut')).toBeNull()
    expect(normalizeCut({ start_s: Number.NaN, end_s: 2 })).toBeNull()
    expect(normalizeCut({ start_s: 5, end_s: 2 })).toBeNull()
    expect(normalizeCut({ start_s: -1, end_s: 2 })).toBeNull()
  })
})

describe('newCutId', () => {
  it('is unique across calls', () => {
    const ids = new Set(Array.from({ length: 200 }, () => newCutId()))
    expect(ids.size).toBe(200)
  })
})

describe('removeCutById', () => {
  it('removes by identity, not position', () => {
    const a = cut({ id: 'a' })
    const b = cut({ id: 'b', start_s: 5, end_s: 6 })
    expect(removeCutById([a, b], 'a')).toEqual([b])
    expect(removeCutById([a, b], 'missing')).toHaveLength(2)
  })
})

describe('adjustCutEdge', () => {
  const c = cut({ id: 'c', start_s: 4, end_s: 8 })

  it('moves the requested edge and leaves the other alone', () => {
    expect(adjustCutEdge([c], 'c', 'start', 5, 20)[0]).toMatchObject({ start_s: 5, end_s: 8 })
    expect(adjustCutEdge([c], 'c', 'end', 9, 20)[0]).toMatchObject({ start_s: 4, end_s: 9 })
  })

  it('refuses to collapse the cut below MIN_CUT_S', () => {
    // Dragging the start past the end must stop, not invert the cut.
    expect(adjustCutEdge([c], 'c', 'start', 99, 20)[0].start_s).toBeCloseTo(8 - MIN_CUT_S, 6)
    expect(adjustCutEdge([c], 'c', 'end', -99, 20)[0].end_s).toBeCloseTo(4 + MIN_CUT_S, 6)
  })

  it('clamps to the clip', () => {
    expect(adjustCutEdge([c], 'c', 'start', -5, 20)[0].start_s).toBe(0)
    expect(adjustCutEdge([c], 'c', 'end', 999, 20)[0].end_s).toBe(20)
  })

  it('does not merge — that happens once, on gesture completion', () => {
    // Merging mid-drag would retire the id under the user's finger.
    const neighbour = cut({ id: 'n', start_s: 8.5, end_s: 9 })
    const out = adjustCutEdge([c, neighbour], 'c', 'end', 8.51, 20)
    expect(out).toHaveLength(2)
    expect(out.map((x) => x.id)).toEqual(['c', 'n'])
  })

  it('leaves other cuts untouched by identity', () => {
    const other = cut({ id: 'other', start_s: 1, end_s: 2 })
    const out = adjustCutEdge([other, c], 'c', 'start', 5, 20)
    expect(out[0]).toBe(other)
  })
})

describe('timeRangeToIndices / cutFromRange', () => {
  it('snaps outward — clipping any part of a word takes the whole word', () => {
    expect(timeRangeToIndices(WORDS, 0.5, 1.5)).toEqual([0, 1])
  })

  it('returns null when a range covers no word', () => {
    expect(timeRangeToIndices(WORDS, 3, 4)).toBeNull()
  })

  it('OMITS indices rather than defaulting to [0, 0] over a gap', () => {
    // The old code used `?? [0, 0]`, which silently struck through the first
    // word of the transcript and labelled the cut with it, on every gap drag.
    const overGap = cutFromRange(WORDS, 3, 4)
    expect(overGap.indices).toBeUndefined()
    expect(cutWordIndices([overGap]).has(0)).toBe(false)

    const overWords = cutFromRange(WORDS, 0.5, 1.5)
    expect(overWords.indices).toEqual([0, 1])
  })
})

describe('cutWordIndices', () => {
  it('collects every covered index and ignores span-less cuts', () => {
    const set = cutWordIndices([cut({ indices: [0, 2] }), cut({ start_s: 9, end_s: 10 })])
    expect([...set].sort()).toEqual([0, 1, 2])
  })
})
