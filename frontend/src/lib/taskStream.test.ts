import { afterEach, describe, expect, it, vi } from 'vitest'
import { subscribeToChatStream } from './taskStream'

// Minimal EventSource fake: records listeners, lets the test drive `error`/`done`
// events and the readyState the resilient reconnect logic branches on.
class FakeEventSource {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSED = 2
  static instances: FakeEventSource[] = []
  url: string
  readyState = FakeEventSource.CONNECTING
  onopen: (() => void) | null = null
  closed = false
  private listeners: Record<string, ((e: unknown) => void)[]> = {}

  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }
  addEventListener(type: string, cb: (e: unknown) => void) {
    ;(this.listeners[type] ||= []).push(cb)
  }
  emit(type: string, data?: string) {
    ;(this.listeners[type] || []).forEach((cb) => cb({ data }))
  }
  open() {
    this.readyState = FakeEventSource.OPEN
    this.onopen?.()
  }
  close() {
    this.closed = true
    this.readyState = FakeEventSource.CLOSED
  }
}

function latest() {
  return FakeEventSource.instances[FakeEventSource.instances.length - 1]
}

afterEach(() => {
  FakeEventSource.instances = []
  vi.unstubAllGlobals()
})

describe('subscribeToChatStream resilience', () => {
  function setup() {
    vi.stubGlobal('EventSource', FakeEventSource)
    const onToken = vi.fn()
    const onDone = vi.fn()
    const onError = vi.fn()
    const onReconnecting = vi.fn()
    const sub = subscribeToChatStream('/tasks/t/events', {
      onToken,
      onDone,
      onError,
      onReconnecting,
    })
    return { es: latest(), onToken, onDone, onError, onReconnecting, sub }
  }

  it('streams tokens and completes on done', () => {
    const { es, onToken, onDone, onError } = setup()
    es.open()
    es.emit('token', JSON.stringify({ chunk: 'hi' }))
    es.emit('done')
    expect(onToken).toHaveBeenCalledWith('hi')
    expect(onDone).toHaveBeenCalledTimes(1)
    expect(onError).not.toHaveBeenCalled()
  })

  it('does NOT surface a terminal error on a transient transport drop', () => {
    const { es, onError, onReconnecting } = setup()
    es.open()
    // Transport blip while the browser is auto-reconnecting (CONNECTING, no data).
    es.readyState = FakeEventSource.CONNECTING
    es.emit('error', undefined)
    expect(onReconnecting).toHaveBeenCalledWith(1)
    expect(onError).not.toHaveBeenCalled()
  })

  it('surfaces "Connection lost." only after exhausting reconnect attempts', () => {
    const { es, onError } = setup()
    es.open()
    es.readyState = FakeEventSource.CONNECTING
    for (let i = 0; i < 6; i++) es.emit('error', undefined)
    expect(onError).toHaveBeenCalledTimes(1)
    expect(onError).toHaveBeenCalledWith('Connection lost.')
  })

  it('treats a server-sent named error as terminal immediately', () => {
    const { es, onError, onReconnecting } = setup()
    es.open()
    es.emit('error', JSON.stringify({ message: 'rate limited' }))
    expect(onReconnecting).not.toHaveBeenCalled()
    expect(onError).toHaveBeenCalledWith('rate limited')
  })

  it('resets the attempt budget after a successful reconnect', () => {
    const { es, onError, onReconnecting } = setup()
    es.open()
    es.readyState = FakeEventSource.CONNECTING
    es.emit('error', undefined) // attempt 1
    es.emit('error', undefined) // attempt 2
    es.open() // reconnected → budget resets
    for (let i = 0; i < 5; i++) es.emit('error', undefined) // 5 more, still under cap
    expect(onError).not.toHaveBeenCalled()
    expect(onReconnecting).toHaveBeenCalledTimes(7)
  })
})
