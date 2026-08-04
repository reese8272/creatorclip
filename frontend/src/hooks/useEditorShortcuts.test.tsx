import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { __busIsListening, shortcutKey, useEditorShortcuts, type ShortcutMap } from './useEditorShortcuts'

function press(init: KeyboardEventInit & { key: string }, target: EventTarget = document.body) {
  const e = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init })
  target.dispatchEvent(e)
  return e
}

function Host({ handlers, priority }: { handlers: ShortcutMap; priority?: number }) {
  useEditorShortcuts(handlers, priority === undefined ? undefined : { priority })
  return null
}

afterEach(() => {
  document.body.innerHTML = ''
})

describe('shortcutKey', () => {
  it('folds Cmd and Ctrl to one `mod` token so a binding covers both platforms', () => {
    expect(shortcutKey(new KeyboardEvent('keydown', { key: 'z', metaKey: true }))).toBe('mod+z')
    expect(shortcutKey(new KeyboardEvent('keydown', { key: 'z', ctrlKey: true }))).toBe('mod+z')
  })

  it('lowercases single characters — with Shift held, e.key for Z is "Z"', () => {
    // The bug this prevents: Shift+Cmd+Z (redo) silently not matching 'z'.
    expect(shortcutKey(new KeyboardEvent('keydown', { key: 'Z', metaKey: true, shiftKey: true }))).toBe(
      'mod+shift+z',
    )
  })

  it('leaves named keys alone', () => {
    expect(shortcutKey(new KeyboardEvent('keydown', { key: 'ArrowRight' }))).toBe('ArrowRight')
    expect(shortcutKey(new KeyboardEvent('keydown', { key: 'Delete', altKey: true }))).toBe('alt+Delete')
  })
})

describe('useEditorShortcuts', () => {
  it('claims a bound key and calls preventDefault so outer layers yield', () => {
    const onZoom = vi.fn(() => true)
    render(<Host handlers={{ '=': onZoom }} />)

    const e = press({ key: '=' })
    expect(onZoom).toHaveBeenCalledTimes(1)
    expect(e.defaultPrevented).toBe(true)
  })

  it('leaves an unbound key — and a handler that declines — alone', () => {
    const declines = vi.fn(() => false)
    render(<Host handlers={{ i: declines }} />)

    const unbound = press({ key: 'q' })
    expect(unbound.defaultPrevented).toBe(false)

    const declined = press({ key: 'i' })
    expect(declines).toHaveBeenCalledTimes(1)
    // A handler returning false means "not mine after all" — the key must stay
    // available to the player and the browser.
    expect(declined.defaultPrevented).toBe(false)
  })

  it('bails on an already-claimed key, a typing target, and an open modal', () => {
    const handler = vi.fn(() => true)
    render(<Host handlers={{ ' ': handler, Delete: handler }} />)

    // 1. already claimed
    const claimed = new KeyboardEvent('keydown', { key: ' ', bubbles: true, cancelable: true })
    claimed.preventDefault()
    document.body.dispatchEvent(claimed)
    expect(handler).not.toHaveBeenCalled()

    // 2. typing target — the key-theft guard
    const input = document.createElement('input')
    document.body.appendChild(input)
    press({ key: ' ' }, input)
    expect(handler).not.toHaveBeenCalled()

    // 3. a modal owns the page's keys wherever focus is
    const dialog = document.createElement('div')
    dialog.setAttribute('role', 'dialog')
    dialog.setAttribute('aria-modal', 'true')
    document.body.appendChild(dialog)
    press({ key: 'Delete' })
    expect(handler).not.toHaveBeenCalled()

    dialog.remove()
    press({ key: 'Delete' })
    expect(handler).toHaveBeenCalledTimes(1)
  })

  it('runs in the CAPTURE phase, so it beats a document listener registered FIRST', () => {
    // This is the whole reason for capture. VideoPlayer mounts before Timeline,
    // so its bubble-phase document listener registers first; on registration
    // order alone it would win ArrowRight.
    const order: string[] = []
    const bubbleFirst = (e: KeyboardEvent) => {
      if (e.defaultPrevented) return
      order.push('player')
    }
    document.addEventListener('keydown', bubbleFirst)
    try {
      render(<Host handlers={{ ArrowRight: () => (order.push('timeline'), true) }} />)
      press({ key: 'ArrowRight' })
      expect(order).toEqual(['timeline'])
    } finally {
      document.removeEventListener('keydown', bubbleFirst)
    }
  })

  it('gives a later-mounted scope precedence at equal priority', () => {
    const first = vi.fn(() => true)
    const second = vi.fn(() => true)
    render(
      <>
        <Host handlers={{ i: first }} />
        <Host handlers={{ i: second }} />
      </>,
    )
    press({ key: 'i' })
    expect(second).toHaveBeenCalledTimes(1)
    expect(first).not.toHaveBeenCalled()
  })

  it('falls through to a lower scope when the higher one declines', () => {
    const outer = vi.fn(() => true)
    const inner = vi.fn(() => false)
    render(
      <>
        <Host handlers={{ i: outer }} />
        <Host handlers={{ i: inner }} />
      </>,
    )
    press({ key: 'i' })
    expect(inner).toHaveBeenCalledTimes(1)
    expect(outer).toHaveBeenCalledTimes(1)
  })

  it('installs one listener lazily and removes it on the last unmount', () => {
    expect(__busIsListening()).toBe(false)
    const a = render(<Host handlers={{ i: () => true }} />)
    expect(__busIsListening()).toBe(true)
    const b = render(<Host handlers={{ o: () => true }} />)
    a.unmount()
    expect(__busIsListening()).toBe(true)
    b.unmount()
    expect(__busIsListening()).toBe(false)
  })

  it('does not re-render the host on a keystroke', () => {
    // The player reports time through a ref specifically to avoid re-rendering
    // the editor 4x/second; a bus that re-rendered on every key would undo that.
    const renders = vi.fn()
    function Counting() {
      renders()
      useEditorShortcuts({ i: () => true })
      return <span>host</span>
    }
    render(<Counting />)
    expect(renders).toHaveBeenCalledTimes(1)

    press({ key: 'i' })
    press({ key: 'i' })
    expect(renders).toHaveBeenCalledTimes(1)
    expect(screen.getByText('host')).toBeInTheDocument()
  })

  it('always sees the latest handlers without resubscribing', () => {
    const first = vi.fn(() => true)
    const second = vi.fn(() => true)
    const { rerender } = render(<Host handlers={{ i: first }} />)
    rerender(<Host handlers={{ i: second }} />)
    press({ key: 'i' })
    expect(second).toHaveBeenCalledTimes(1)
    expect(first).not.toHaveBeenCalled()
  })
})
