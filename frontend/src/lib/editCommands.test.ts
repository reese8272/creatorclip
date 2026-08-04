import { describe, expect, it } from 'vitest'

import {
  canRedo,
  canUndo,
  commit,
  initHistory,
  redo,
  reset,
  undo,
} from './editCommands'
import type { EditDocument } from '@/types'

function doc(...ids: string[]): EditDocument {
  return {
    version: 1,
    cuts: ids.map((id, i) => ({ id, start_s: i * 10, end_s: i * 10 + 1 })),
    last_applied_at: null,
  }
}

const addCut = (id: string) => (current: EditDocument) => ({
  ...current,
  cuts: [...current.cuts, { id, start_s: 99, end_s: 100 }],
})

describe('editCommands', () => {
  it('starts with nothing to undo or redo', () => {
    const h = initHistory(doc())
    expect(canUndo(h)).toBe(false)
    expect(canRedo(h)).toBe(false)
  })

  it('undoes MORE THAN ONE step — the defect this replaces', () => {
    // The old single-slot undo made exactly this sequence impossible.
    let h = initHistory(doc())
    h = commit(h, addCut('a'))
    h = commit(h, addCut('b'))
    expect(h.present.cuts.map((c) => c.id)).toEqual(['a', 'b'])

    h = undo(h)
    expect(h.present.cuts.map((c) => c.id)).toEqual(['a'])
    h = undo(h)
    expect(h.present.cuts).toEqual([])
    expect(canUndo(h)).toBe(false)
  })

  it('redoes back up the stack', () => {
    let h = initHistory(doc())
    h = commit(h, addCut('a'))
    h = commit(h, addCut('b'))
    h = undo(h)
    h = undo(h)

    expect(canRedo(h)).toBe(true)
    h = redo(h)
    expect(h.present.cuts.map((c) => c.id)).toEqual(['a'])
    h = redo(h)
    expect(h.present.cuts.map((c) => c.id)).toEqual(['a', 'b'])
    expect(canRedo(h)).toBe(false)
  })

  it('clears the redo branch on a new commit after undo', () => {
    let h = initHistory(doc())
    h = commit(h, addCut('a'))
    h = undo(h)
    expect(canRedo(h)).toBe(true)

    h = commit(h, addCut('different'))
    // The undone branch is unreachable — redoing to it would jump to a document
    // that never followed from what is on screen.
    expect(canRedo(h)).toBe(false)
    expect(h.present.cuts.map((c) => c.id)).toEqual(['different'])
  })

  it('does not add a history step for a no-op commit', () => {
    let h = initHistory(doc('a'))
    h = commit(h, addCut('b'))
    const before = h
    h = commit(h, (current) => current)
    expect(h).toBe(before)
    expect(h.past).toHaveLength(1)
  })

  it('never mutates a document already in the history', () => {
    // Snapshots are only safe if past entries are untouched. Freezing proves a
    // producer cannot reach back through a shared reference — which is exactly
    // how the pre-390 mergeAdjacent corrupted the single undo slot.
    const start = Object.freeze(doc('a')) as EditDocument
    Object.freeze(start.cuts)
    let h = initHistory(start)
    h = commit(h, addCut('b'))
    h = commit(h, addCut('c'))

    expect(h.past[0]).toBe(start)
    expect(start.cuts.map((c) => c.id)).toEqual(['a'])
  })

  it('is unbounded within a session', () => {
    let h = initHistory(doc())
    for (let i = 0; i < 250; i++) h = commit(h, addCut(`c${i}`))
    expect(h.past).toHaveLength(250)
    for (let i = 0; i < 250; i++) h = undo(h)
    expect(h.present.cuts).toEqual([])
  })

  it('undo and redo are no-ops at the ends rather than throwing', () => {
    const h = initHistory(doc('a'))
    expect(undo(h)).toBe(h)
    expect(redo(h)).toBe(h)
  })

  it('reset drops history so an undo cannot cross clips', () => {
    // The pre-391 single slot was not cleared on clip change, so undo on clip B
    // could restore clip A's cuts onto it.
    const clipA = commit(initHistory(doc('clipA-cut')), addCut('another'))
    expect(canUndo(clipA)).toBe(true)

    const clipB = reset(doc('clipB-cut'))
    expect(canUndo(clipB)).toBe(false)
    expect(canRedo(clipB)).toBe(false)
    expect(clipB.present.cuts.map((c) => c.id)).toEqual(['clipB-cut'])
  })
})
