import { describe, expect, it, vi } from 'vitest'
import { flushResizeObservers } from './setup'

// The old stub was a no-op that could never fire, which silently made every
// width-driven component untestable — a zoom-math assertion would pass against a
// container of width 0 for the wrong reason. Pin the fake's contract so Issue 390
// can rely on it.
describe('ResizeObserver test fake', () => {
  it('fires observed targets with the flushed width', () => {
    const seen: number[] = []
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) seen.push(e.contentRect.width)
    })
    ro.observe(document.createElement('div'))

    expect(flushResizeObservers(880)).toBe(1)
    expect(seen).toEqual([880])

    flushResizeObservers(440)
    expect(seen).toEqual([880, 440])
  })

  it('does not fire an observer with no observed targets, or after disconnect', () => {
    const cb = vi.fn()
    const ro = new ResizeObserver(cb)
    expect(flushResizeObservers(800)).toBe(0)

    const el = document.createElement('div')
    ro.observe(el)
    flushResizeObservers(800)
    expect(cb).toHaveBeenCalledTimes(1)

    ro.disconnect()
    expect(flushResizeObservers(800)).toBe(0)
    expect(cb).toHaveBeenCalledTimes(1)
  })
})
