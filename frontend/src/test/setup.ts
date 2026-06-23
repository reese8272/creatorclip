// Vitest global setup: registers @testing-library/jest-dom matchers
// (toBeInTheDocument, toHaveTextContent, …) and auto-cleans the DOM between
// tests so component renders don't leak into each other.
import '@testing-library/jest-dom/vitest'
import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'

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

afterEach(() => {
  cleanup()
})
