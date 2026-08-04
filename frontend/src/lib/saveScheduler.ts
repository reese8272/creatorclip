// The autosave cadence (Issue 391): trailing debounce WITH a max-wait ceiling.
//
// WHY MAX-WAIT IS NOT OPTIONAL. A pure trailing debounce only fires once
// activity stops. Someone dragging cut edges continuously for 90 seconds would
// therefore produce ZERO saves — which is precisely the "your work vanished"
// failure this issue exists to remove. The second timer guarantees a save every
// `maxWaitMs` no matter how continuous the editing is.
//
// WHY THIS IS HAND-ROLLED. lodash is not in the dependency tree, and its
// `debounce` has open correctness bugs in exactly this combination — missed
// calls after a maxWait fire (lodash#2229) and unexpected leading-edge emission
// on later bursts (lodash#2089). Twenty lines with two timers is both smaller
// than the dependency and testable with fake timers.

/** Trailing debounce interval — how long after the last edit we save. */
export const SAVE_DEBOUNCE_MS = 800

/** Ceiling between saves during CONTINUOUS editing. See the note above. */
export const SAVE_MAX_WAIT_MS = 2000

export interface SaveScheduler {
  /** Mark dirty and (re)arm the timers. */
  schedule: () => void
  /** Fire immediately if anything is pending; otherwise do nothing. */
  flush: () => void
  /** Drop anything pending without firing. */
  cancel: () => void
  isPending: () => boolean
}

export function createSaveScheduler(
  onFire: () => void,
  { waitMs = SAVE_DEBOUNCE_MS, maxWaitMs = SAVE_MAX_WAIT_MS } = {},
): SaveScheduler {
  let trailing: ReturnType<typeof setTimeout> | null = null
  let ceiling: ReturnType<typeof setTimeout> | null = null

  function clearTimers() {
    if (trailing !== null) clearTimeout(trailing)
    if (ceiling !== null) clearTimeout(ceiling)
    trailing = null
    ceiling = null
  }

  function fire() {
    clearTimers()
    onFire()
  }

  return {
    schedule() {
      // The trailing timer restarts on every edit; the ceiling does NOT, so a
      // continuous burst still fires on schedule. Arming the ceiling only when
      // it is idle is what makes it a ceiling rather than a second debounce.
      if (trailing !== null) clearTimeout(trailing)
      trailing = setTimeout(fire, waitMs)
      if (ceiling === null) ceiling = setTimeout(fire, maxWaitMs)
    },
    flush() {
      if (trailing === null && ceiling === null) return
      fire()
    },
    cancel: clearTimers,
    isPending: () => trailing !== null || ceiling !== null,
  }
}
