import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  subscribe,
  getSnapshot,
  upsert,
  remove,
  openSubscriptionCount,
  isCapExhausted,
  MAX_CONCURRENT_SSE,
  _reset,
} from './activeTasks'

// Issue 211 — activeTasks store unit tests.
// Guards: subscribe/getSnapshot contract (useSyncExternalStore), upsert merges,
// auto-removal after TERMINAL_TTL_MS, remove(), cap exhaustion helpers, and
// notification on every mutating operation.

beforeEach(() => {
  vi.useFakeTimers()
  _reset()
})
afterEach(() => {
  vi.useRealTimers()
  _reset()
})

describe('subscribe / getSnapshot contract', () => {
  it('getSnapshot returns an empty Map initially', () => {
    expect(getSnapshot().size).toBe(0)
  })

  it('subscribe callback fires on upsert', () => {
    const cb = vi.fn()
    const unsub = subscribe(cb)
    upsert('t1', { videoId: 'v1', phase: 'running' })
    expect(cb).toHaveBeenCalledTimes(1)
    unsub()
  })

  it('unsubscribing stops further notifications', () => {
    const cb = vi.fn()
    const unsub = subscribe(cb)
    unsub()
    upsert('t1', { videoId: 'v1', phase: 'running' })
    expect(cb).not.toHaveBeenCalled()
  })

  it('getSnapshot reference changes on every mutation', () => {
    const snap1 = getSnapshot()
    upsert('t1', { videoId: 'v1' })
    const snap2 = getSnapshot()
    expect(snap1).not.toBe(snap2)
  })
})

describe('upsert', () => {
  it('adds a new entry with defaults for omitted fields', () => {
    upsert('t1', { videoId: 'v1' })
    const entry = getSnapshot().get('t1')
    expect(entry).toBeDefined()
    expect(entry!.videoId).toBe('v1')
    expect(entry!.phase).toBe('pending')
    expect(entry!.label).toBeNull()
    expect(entry!.stage).toBeNull()
    expect(entry!.subscribed).toBe(false)
  })

  it('merges a partial patch without overwriting unset fields', () => {
    upsert('t1', { videoId: 'v1', phase: 'running', stage: 'ingest' })
    upsert('t1', { label: 'step-label' })
    const entry = getSnapshot().get('t1')!
    expect(entry.stage).toBe('ingest')
    expect(entry.label).toBe('step-label')
    expect(entry.phase).toBe('running')
  })

  it('can explicitly set label to null to clear it', () => {
    upsert('t1', { videoId: 'v1', label: 'something' })
    upsert('t1', { label: null })
    expect(getSnapshot().get('t1')!.label).toBeNull()
  })
})

describe('auto-removal on terminal phase', () => {
  it('removes a done entry after the TTL', () => {
    upsert('t1', { videoId: 'v1', phase: 'done' })
    expect(getSnapshot().has('t1')).toBe(true)
    vi.runAllTimers()
    expect(getSnapshot().has('t1')).toBe(false)
  })

  it('removes an error entry after the TTL', () => {
    upsert('t1', { videoId: 'v1', phase: 'error' })
    vi.runAllTimers()
    expect(getSnapshot().has('t1')).toBe(false)
  })

  it('notifies subscribers when the auto-removal fires', () => {
    upsert('t1', { videoId: 'v1', phase: 'done' })
    const cb = vi.fn()
    const unsub = subscribe(cb)
    cb.mockClear()
    vi.runAllTimers()
    expect(cb).toHaveBeenCalled()
    unsub()
  })

  it('keeps a running entry alive (no auto-removal)', () => {
    upsert('t1', { videoId: 'v1', phase: 'running' })
    vi.runAllTimers()
    expect(getSnapshot().has('t1')).toBe(true)
  })
})

describe('remove()', () => {
  it('immediately removes an entry and notifies', () => {
    const cb = vi.fn()
    const unsub = subscribe(cb)
    upsert('t1', { videoId: 'v1', phase: 'running' })
    cb.mockClear()

    remove('t1')

    expect(getSnapshot().has('t1')).toBe(false)
    expect(cb).toHaveBeenCalledTimes(1)
    unsub()
  })

  it('is a no-op when the entry does not exist', () => {
    const cb = vi.fn()
    const unsub = subscribe(cb)
    remove('nonexistent')
    expect(cb).not.toHaveBeenCalled()
    unsub()
  })
})

describe('SSE cap helpers', () => {
  it('openSubscriptionCount counts only subscribed=true entries', () => {
    upsert('t1', { videoId: 'v1', subscribed: true })
    upsert('t2', { videoId: 'v2', subscribed: false })
    upsert('t3', { videoId: 'v3', subscribed: true })
    expect(openSubscriptionCount()).toBe(2)
  })

  it('isCapExhausted returns true when open count reaches MAX_CONCURRENT_SSE', () => {
    for (let i = 0; i < MAX_CONCURRENT_SSE; i++) {
      upsert(`t${i}`, { videoId: `v${i}`, subscribed: true })
    }
    expect(isCapExhausted()).toBe(true)
  })

  it('isCapExhausted returns false when below the cap', () => {
    upsert('t1', { videoId: 'v1', subscribed: true })
    expect(isCapExhausted()).toBe(false)
  })
})
