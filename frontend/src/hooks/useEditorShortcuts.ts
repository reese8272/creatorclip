import { useEffect, useRef } from 'react'
import { isModalOpen, isTypingTarget } from '@/lib/keyboard'

/**
 * Return `true` from a handler to claim the key. The bus then calls
 * `preventDefault()`, which is what makes the other keyboard layers yield.
 */
export type ShortcutHandler = (e: KeyboardEvent) => boolean

export type ShortcutMap = Record<string, ShortcutHandler>

/**
 * A canonical key string: lowercased `e.key`, prefixed with held modifiers in a
 * fixed order. `mod` is Cmd on macOS and Ctrl elsewhere, so one binding covers
 * both — `mod+z` matches Cmd+Z and Ctrl+Z.
 *
 * Lowercasing matters: with Shift held, `e.key` for the Z key is `'Z'`, so a
 * naive `e.key === 'z'` check silently misses every Shift+Cmd+Z (redo).
 */
export function shortcutKey(e: KeyboardEvent): string {
  const parts: string[] = []
  if (e.metaKey || e.ctrlKey) parts.push('mod')
  if (e.altKey) parts.push('alt')
  if (e.shiftKey) parts.push('shift')
  parts.push(e.key.length === 1 ? e.key.toLowerCase() : e.key)
  return parts.join('+')
}

// ── The bus ──────────────────────────────────────────────────────────────────
//
// A module-level singleton with ONE document listener, installed lazily on the
// first subscriber and removed on the last.
//
// WHY NOT REACT CONTEXT. A provider above the player would re-render its
// consumers, and `video-player.test.tsx` asserts that a consumer renders exactly
// once — the player reports time through a ref precisely to avoid re-rendering
// the editor 4×/second. A module singleton has no render surface at all.
//
// WHY CAPTURE PHASE. `VideoPlayer` mounts before `Timeline` in both editors, so
// its bubble-phase document listener registers first and would win ←/→ on
// registration order alone. A capture-phase listener always precedes any
// bubble-phase listener on `document`, so precedence is structural rather than
// an accident of mount order that a refactor could flip.

interface Scope {
  handlersRef: { current: ShortcutMap }
  /** Higher wins. Later-mounted scopes of equal priority win (LIFO). */
  priority: number
  seq: number
}

const scopes = new Set<Scope>()
let seqCounter = 0
let listening = false

function dispatch(e: KeyboardEvent): void {
  // Bail order is deliberate and mirrors the player's own listener:
  //  1. someone nearer the target already claimed this key
  //  2. the user is typing — never steal from a text surface
  //  3. a modal owns the whole page's keys regardless of where focus is
  if (e.defaultPrevented) return
  if (isTypingTarget(e.target)) return
  if (isModalOpen()) return

  const key = shortcutKey(e)
  const ordered = [...scopes].sort((a, b) => b.priority - a.priority || b.seq - a.seq)
  for (const scope of ordered) {
    const handler = scope.handlersRef.current[key]
    if (handler && handler(e)) {
      e.preventDefault()
      return
    }
  }
}

function ensureListening(): void {
  if (listening) return
  document.addEventListener('keydown', dispatch, true)
  listening = true
}

function stopListeningIfIdle(): void {
  if (scopes.size > 0 || !listening) return
  document.removeEventListener('keydown', dispatch, true)
  listening = false
}

/**
 * Register a set of editor shortcuts for as long as the component is mounted.
 *
 * `handlers` is read through a ref that is refreshed on every render, so the
 * subscription identity never changes and a keystroke never causes a re-render
 * in the host component. Pass a plain object literal — it does not need to be
 * memoised.
 */
export function useEditorShortcuts(handlers: ShortcutMap, { priority = 0 } = {}): void {
  const handlersRef = useRef(handlers)
  // Refreshed in an effect, not during render: writing to a ref while rendering
  // is a side effect React is free to run more than once. Safe for this use —
  // keystrokes are user events, which always arrive after commit.
  useEffect(() => {
    handlersRef.current = handlers
  })

  useEffect(() => {
    const scope: Scope = { handlersRef, priority, seq: seqCounter++ }
    scopes.add(scope)
    ensureListening()
    return () => {
      scopes.delete(scope)
      stopListeningIfIdle()
    }
  }, [priority])
}

/** Test seam: assert the bus tore itself down rather than leaking a listener. */
export function __busIsListening(): boolean {
  return listening
}
