import type { EditDocument } from '@/types'

// The editor's undo/redo history (Issue 391).
//
// Replaces `useState<EditorCut[] | null>` — a single slot that each mutation
// overwrote and undo cleared, so two cuts followed by two undos was impossible.
// For a tool whose entire interaction model is "make destructive edits", one
// level of undo is the wrong order of magnitude.
//
// SNAPSHOTS, NOT INVERSE FUNCTIONS. The document is well under a kilobyte, so
// the memory argument for an inverse-function stack does not apply here — and
// the correctness argument runs the other way. An inverse stack needs EVERY
// future command type (#396's reframe overrides, overlays, music) to ship a
// correct inverse, and an inverse bug is silent, sequence-dependent, and
// surfaces as "undo mangled my edit" long after the commit that caused it. A
// snapshot cannot be wrong about what the past looked like.

/** The history: everything before `present`, `present`, everything undone. */
export interface EditHistory {
  past: EditDocument[]
  present: EditDocument
  future: EditDocument[]
}

/** Produce the next document from the current one. Must not mutate its input. */
export type DocumentProducer = (current: EditDocument) => EditDocument

export function initHistory(present: EditDocument): EditHistory {
  return { past: [], present, future: [] }
}

/**
 * Commit a new present, pushing the old one onto `past` and clearing `future`.
 *
 * Clearing `future` is the standard linear-history rule: once you edit after
 * undoing, the branch you undid is unreachable and keeping it would let redo
 * jump to a document that never followed from what is on screen.
 *
 * THIS IS A GESTURE-COMPLETION API, NOT A PER-FRAME API. An edge drag holds its
 * own local preview and calls this ONCE, on pointerup. Calling it per
 * pointermove would push ~60 entries per second and make a single undo look
 * like it did nothing. There is deliberately no time-window merge heuristic to
 * paper over that: those are timing-dependent, and a test for one is either
 * flaky or asserts the mock clock rather than the behaviour.
 *
 * Returns the same history object when the producer is a no-op, so a caller
 * that commits an unchanged document does not add a dead undo step.
 */
export function commit(history: EditHistory, produce: DocumentProducer): EditHistory {
  const next = produce(history.present)
  if (next === history.present) return history
  return { past: [...history.past, history.present], present: next, future: [] }
}

export function canUndo(history: EditHistory): boolean {
  return history.past.length > 0
}

export function canRedo(history: EditHistory): boolean {
  return history.future.length > 0
}

export function undo(history: EditHistory): EditHistory {
  if (history.past.length === 0) return history
  const previous = history.past[history.past.length - 1]
  return {
    past: history.past.slice(0, -1),
    present: previous,
    future: [history.present, ...history.future],
  }
}

export function redo(history: EditHistory): EditHistory {
  if (history.future.length === 0) return history
  const [next, ...rest] = history.future
  return { past: [...history.past, history.present], present: next, future: rest }
}

/**
 * Discard all history and start again from `present`.
 *
 * Called on clip change. Before this issue the single undo slot was NOT cleared
 * when the selected clip changed, so pressing undo on clip B could restore clip
 * A's cuts onto it. The structural fix is `key={clip.id}` on the editor subtree
 * — which remounts and re-runs `initHistory` — and this exists for the paths
 * that need to reset without a remount.
 */
export function reset(present: EditDocument): EditHistory {
  return initHistory(present)
}
