import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// The SPA is served by FastAPI under /app/* (see main.py + docs/DECISIONS.md
// 2026-06-17). `base` makes every built asset URL absolute under /app/ so the
// SPA fallback route can live at /app/{path} without breaking asset resolution.
//
// In dev, the API lives on the FastAPI dev server (default :8000); proxy the
// cookie-authed endpoints the SPA calls so `credentials: 'include'` works
// same-origin against the Vite dev server.
const API_TARGET = process.env.VITE_API_TARGET || 'http://localhost:8000'
const API_PREFIXES = ['/auth', '/creators', '/billing', '/tasks', '/api']

export default defineConfig({
  base: '/app/',
  plugins: [react(), tailwindcss()],
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
    globals: false,
    css: false,
  },
})
