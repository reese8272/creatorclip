import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  SAVE_DEBOUNCE_MS,
  SAVE_MAX_WAIT_MS,
  createSaveScheduler,
} from './saveScheduler'

describe('createSaveScheduler', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('fires once, after the quiet period', () => {
    const onFire = vi.fn()
    const s = createSaveScheduler(onFire)
    s.schedule()

    vi.advanceTimersByTime(SAVE_DEBOUNCE_MS - 1)
    expect(onFire).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(onFire).toHaveBeenCalledTimes(1)
  })

  it('coalesces a burst of edits into one save', () => {
    const onFire = vi.fn()
    const s = createSaveScheduler(onFire)
    for (let i = 0; i < 5; i++) {
      s.schedule()
      vi.advanceTimersByTime(100)
    }
    expect(onFire).not.toHaveBeenCalled()
    vi.advanceTimersByTime(SAVE_DEBOUNCE_MS)
    expect(onFire).toHaveBeenCalledTimes(1)
  })

  it('STILL SAVES during continuous editing — the max-wait ceiling', () => {
    // The defect a pure trailing debounce would reintroduce: 90 seconds of
    // uninterrupted dragging producing zero saves.
    const onFire = vi.fn()
    const s = createSaveScheduler(onFire)

    for (let elapsed = 0; elapsed < 10_000; elapsed += 100) {
      s.schedule()
      vi.advanceTimersByTime(100)
    }

    // ~one save per max-wait window, never zero.
    expect(onFire.mock.calls.length).toBeGreaterThanOrEqual(10_000 / SAVE_MAX_WAIT_MS - 1)
  })

  it('re-arms cleanly after firing', () => {
    const onFire = vi.fn()
    const s = createSaveScheduler(onFire)
    s.schedule()
    vi.advanceTimersByTime(SAVE_DEBOUNCE_MS)
    expect(onFire).toHaveBeenCalledTimes(1)
    expect(s.isPending()).toBe(false)

    s.schedule()
    vi.advanceTimersByTime(SAVE_DEBOUNCE_MS)
    expect(onFire).toHaveBeenCalledTimes(2)
  })

  it('flush fires immediately when pending', () => {
    const onFire = vi.fn()
    const s = createSaveScheduler(onFire)
    s.schedule()
    s.flush()
    expect(onFire).toHaveBeenCalledTimes(1)
    // and cancels the timers, so no second fire lands later
    vi.advanceTimersByTime(SAVE_MAX_WAIT_MS * 2)
    expect(onFire).toHaveBeenCalledTimes(1)
  })

  it('flush is a no-op when nothing is pending', () => {
    const onFire = vi.fn()
    const s = createSaveScheduler(onFire)
    s.flush()
    expect(onFire).not.toHaveBeenCalled()
  })

  it('cancel drops the pending save without firing', () => {
    const onFire = vi.fn()
    const s = createSaveScheduler(onFire)
    s.schedule()
    s.cancel()
    vi.advanceTimersByTime(SAVE_MAX_WAIT_MS * 2)
    expect(onFire).not.toHaveBeenCalled()
    expect(s.isPending()).toBe(false)
  })
})
