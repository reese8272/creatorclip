import { readFileSync } from 'node:fs'
import { fileURLToPath, URL } from 'node:url'
import { defineConfig, type Plugin } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Exposes the design-token stylesheet to the test suite as plain text, so
// src/test/design-tokens.contract.test.ts can check that every colour utility
// names a token index.css actually declares (Issue 400a).
//
// A plain `?raw` import does not work: `test.css: false` short-circuits anything
// ending in .css, and @tailwindcss/vite claims the rest. `enforce: 'pre'` runs
// this resolver ahead of both. Reading the file is fine here — vite.config.ts is
// the one place with @types/node (tsconfig.node.json); src/ has only vite/client.
const VIRTUAL_TOKENS = 'virtual:design-tokens-css'

function designTokensText(): Plugin {
  const resolved = `\0${VIRTUAL_TOKENS}`
  return {
    name: 'design-tokens-text',
    enforce: 'pre',
    resolveId: (id) => (id === VIRTUAL_TOKENS ? resolved : undefined),
    load(id) {
      if (id !== resolved) return undefined
      const css = readFileSync(fileURLToPath(new URL('./src/index.css', import.meta.url)), 'utf8')
      return `export default ${JSON.stringify(css)}`
    },
  }
}

// The SPA is served by FastAPI under /app/* (see main.py + docs/DECISIONS.md
// 2026-06-17). `base` makes every built asset URL absolute under /app/ so the
// SPA fallback route can live at /app/{path} without breaking asset resolution.
//
// In dev, the API lives on the FastAPI dev server (default :8000); proxy the
// cookie-authed endpoints the SPA calls so `credentials: 'include'` works
// same-origin against the Vite dev server.
const API_TARGET = process.env.VITE_API_TARGET || 'http://localhost:8000'
// /videos + /clips were missing until Issue 395 — uploads from the Vite dev
// server could never reach the API (the e2e mock always intercepted them).
const API_PREFIXES = ['/auth', '/creators', '/billing', '/tasks', '/api', '/videos', '/clips']

export default defineConfig({
  base: '/app/',
  plugins: [designTokensText(), react(), tailwindcss()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    proxy: Object.fromEntries(
      API_PREFIXES.map((p) => [p, { target: API_TARGET, changeOrigin: true }]),
    ),
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    // Issue 390: without this a vi.spyOn leaks for the rest of the FILE. The old
    // Timeline.test spied on Element.prototype.getBoundingClientRect and every
    // subsequent test in it saw the same 400x80 rect for every element — which
    // would silently make zoom assertions pass for the wrong reason, since
    // viewportWidth === contentWidth unconditionally.
    restoreMocks: true,
    globals: false,
    css: false,
    // Vitest owns src/ component tests; Playwright owns e2e/ (its own runner and
    // a conflicting `test` export). Keep the two suites from colliding.
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    exclude: ['e2e/**', 'node_modules/**'],
  },
})
