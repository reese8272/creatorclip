// Vitest global setup: registers @testing-library/jest-dom matchers
// (toBeInTheDocument, toHaveTextContent, …) and auto-cleans the DOM between
// tests so component renders don't leak into each other.
import '@testing-library/jest-dom/vitest'
import { afterEach } from 'vitest'
import { cleanup, configure } from '@testing-library/react'

// Raise the testing-library async-utility timeout (default 1000ms) so `findBy*`
// queries don't intermittently time out under full-suite parallel load — the
// content is correct, the async render (react-query mock resolution + effects)
// is just slow to settle when many test files run concurrently (esp. on the
// contended self-hosted CI runner). This makes a known suite-wide timing flake
// — findByRole timeouts on Dashboard/Insights/etc. — deterministic.
configure({ asyncUtilTimeout: 5000 })

// jsdom does not implement window.matchMedia. Provide a minimal stub that
// returns matches=false (motion allowed) by default — good enough for component
// tests that only read `.matches` and don't observe changes.
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
})

// jsdom implements none of the APIs Radix's Select/Popper layer calls during a
// normal open-and-pick interaction (Issue 385). Without these stubs EVERY Select
// test throws `hasPointerCapture is not a function` before it can assert
// anything — the components are fine, the environment just lacks the surface.
Element.prototype.hasPointerCapture ??= () => false
Element.prototype.setPointerCapture ??= () => {}
Element.prototype.releasePointerCapture ??= () => {}
Element.prototype.scrollIntoView ??= () => {}

// Radix's positioning measures its trigger and content, and jsdom implements no
// ResizeObserver at all. The stub used to be a no-op that could never fire, which
// is fine for Radix (we assert on roles and values, never geometry) but makes any
// width-driven component untestable — its callback simply never runs.
//
// Issue 390 rebuilds the Timeline around zoom-to-fit and playhead-in-view, both
// pure functions of the measured container width, and its acceptance criterion is
// "component test covers zoom math at multiple zoom levels". Landed here as 389
// groundwork so that test is possible to write: observers register their targets,
// and `flushResizeObservers(width)` drives them synchronously.
//
// Note the intended usage: read the initial width with getBoundingClientRect in a
// layout effect and treat ResizeObserver purely as the *change* notifier. That way
// first paint is correct even under a fake that has not fired yet.
type ResizeCallback = (entries: ResizeObserverEntry[], observer: ResizeObserver) => void

const resizeObservers = new Set<{ cb: ResizeCallback; targets: Set<Element>; ro: ResizeObserver }>()

globalThis.ResizeObserver ??= class FakeResizeObserver {
  private entry: { cb: ResizeCallback; targets: Set<Element>; ro: ResizeObserver }

  constructor(cb: ResizeCallback) {
    this.entry = { cb, targets: new Set(), ro: this as unknown as ResizeObserver }
    resizeObservers.add(this.entry)
  }
  observe(target: Element) {
    this.entry.targets.add(target)
  }
  unobserve(target: Element) {
    this.entry.targets.delete(target)
  }
  disconnect() {
    this.entry.targets.clear()
    resizeObservers.delete(this.entry)
  }
} as unknown as typeof ResizeObserver

/**
 * Synchronously fire every live ResizeObserver with `width` (and `height`) for
 * each observed target. Returns the number of callbacks invoked so a test can
 * assert it actually drove something rather than silently no-opping.
 */
export function flushResizeObservers(width: number, height = 0): number {
  let fired = 0
  for (const { cb, targets, ro } of resizeObservers) {
    if (targets.size === 0) continue
    const entries = [...targets].map(
      (target) =>
        ({
          target,
          contentRect: { width, height, top: 0, left: 0, right: width, bottom: height, x: 0, y: 0 },
          borderBoxSize: [{ inlineSize: width, blockSize: height }],
          contentBoxSize: [{ inlineSize: width, blockSize: height }],
          devicePixelContentBoxSize: [{ inlineSize: width, blockSize: height }],
        }) as unknown as ResizeObserverEntry,
    )
    cb(entries, ro)
    fired += 1
  }
  return fired
}

// jsdom does not implement HTMLMediaElement playback: play/pause/load throw
// "Not implemented", and currentTime/duration are not usefully settable. The
// VideoPlayer primitive (Issue 386) drives all of them, so stub the surface.
// requestVideoFrameCallback is deliberately left undefined — that keeps the
// fps-measurement path (a browser-only refinement) out of unit tests.
Object.defineProperties(HTMLMediaElement.prototype, {
  play: { writable: true, value: () => Promise.resolve() },
  pause: { writable: true, value: () => {} },
  load: { writable: true, value: () => {} },
  currentTime: { writable: true, value: 0 },
  duration: { writable: true, value: 0 },
  paused: { writable: true, value: true },
  playbackRate: { writable: true, value: 1 },
  muted: { writable: true, value: false },
})

afterEach(() => {
  cleanup()
  resizeObservers.clear()
})
