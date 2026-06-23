import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useTaskStream } from './useTaskStream'

// jsdom has no EventSource, so we stub a minimal fake that records listeners and
// lets a test emit named SSE events. This locks the load-bearing hook behaviour:
// idle with no url, streaming→done transitions, error surfacing, and — the bug
// class the vanilla code kept hitting — that the connection is closed on unmount.
class FakeEventSource {
  static instances: FakeEventSource[] = []
  url: string
  closed = false
  private listeners: Record<string, ((e: MessageEvent) => void)[]> = {}

  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }

  addEventListener(type: string, cb: (e: MessageEvent) => void) {
    ;(this.listeners[type] ??= []).push(cb)
  }

  emit(type: string, data: unknown) {
    const evt = { data: JSON.stringify(data) } as MessageEvent
    ;(this.listeners[type] ?? []).forEach((cb) => cb(evt))
  }

  close() {
    this.closed = true
  }
}

describe('useTaskStream', () => {
  beforeEach(() => {
    FakeEventSource.instances = []
    vi.stubGlobal('EventSource', FakeEventSource)
  })
  afterEach(() => vi.unstubAllGlobals())

  it('stays idle and opens no connection when url is null', () => {
    const { result } = renderHook(() => useTaskStream(null))
    expect(result.current.status).toBe('idle')
    expect(FakeEventSource.instances).toHaveLength(0)
  })

  it('streams progress then resolves to done', () => {
    const { result } = renderHook(() => useTaskStream('/tasks/abc/events'))
    expect(result.current.status).toBe('streaming')

    const es = FakeEventSource.instances[0]
    act(() => es.emit('step', { label: 'ingest' }))
    expect(result.current.buffer).toContain('ingest')
    // steps array is populated by onStep alongside the buffer
    expect(result.current.steps).toEqual(['ingest'])

    act(() => es.emit('done', { version: 3 }))
    expect(result.current.status).toBe('done')
  })

  it('accumulates multiple step labels in order', () => {
    const { result } = renderHook(() => useTaskStream('/tasks/abc/events'))
    const es = FakeEventSource.instances[0]
    act(() => es.emit('step', { label: 'fetch' }))
    act(() => es.emit('step', { label: 'embed' }))
    act(() => es.emit('step', { label: 'persist' }))
    expect(result.current.steps).toEqual(['fetch', 'embed', 'persist'])
  })

  it('starts with empty steps when url is null', () => {
    const { result } = renderHook(() => useTaskStream(null))
    expect(result.current.steps).toEqual([])
  })

  it('surfaces a server error event', () => {
    const { result } = renderHook(() => useTaskStream('/tasks/abc/events'))
    act(() => FakeEventSource.instances[0].emit('error', { message: 'boom' }))
    expect(result.current.status).toBe('error')
    expect(result.current.error).toBe('boom')
  })

  it('closes the EventSource on unmount (no leaked connection)', () => {
    const { unmount } = renderHook(() => useTaskStream('/tasks/abc/events'))
    const es = FakeEventSource.instances[0]
    expect(es.closed).toBe(false)
    unmount()
    expect(es.closed).toBe(true)
  })
})
