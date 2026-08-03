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

// Radix's positioning measures its trigger and content. jsdom reports zero-sized
// rects, which is harmless here — we assert on roles and values, never geometry.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
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
})
