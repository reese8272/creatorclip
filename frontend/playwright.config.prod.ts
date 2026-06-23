import { existsSync } from 'node:fs'
import { defineConfig, devices } from '@playwright/test'

// LIVE-SITE audit config (Issue 164) — points at production, NOT the dev server.
// Unlike playwright.config.ts (mocked backend, Vite dev server), this runs against
// https://autoclip.studio with the REAL FastAPI backend and a REAL authenticated
// session, so it surfaces gaps the mocked harness can't: real data shapes, real
// network/latency, real auth-gated rendering, accessibility on the shipped CSS.
//
// Auth: a one-time manual Google login captures the signed `cc_session` cookie to
// AUTH_FILE (run `npm run test:prod:auth`). Every audit project reuses it via
// storageState — no repeated logins. The file holds a live session token and is
// gitignored (.auth/).
const BASE_URL = 'https://autoclip.studio/app/'
export const AUTH_FILE = 'e2e/.auth/prod.json'

// storageState must not be set to a missing path (Playwright throws). Before the
// first login capture there's no session — projects then run unauthenticated,
// which still exercises the public routes (login, pricing).
const storageState = existsSync(AUTH_FILE) ? AUTH_FILE : undefined

export default defineConfig({
  testDir: './e2e/prod',
  outputDir: './e2e/.results/prod',
  // Live site, real backend: never parallel-hammer it, and don't retry paid flows.
  fullyParallel: false,
  workers: 1,
  retries: 0,
  // Issue 274 (OCB-3): video-analysis and title-optimizer flows timed out at 60s on
  // the live account. LLM generation can take >60s on a slow run. 120s provides
  // headroom without masking genuine outages — if a flow still exceeds 120s,
  // file a dedicated perf issue (do not raise this further).
  timeout: 120_000,
  reporter: [['list'], ['html', { outputFolder: 'e2e/.report-prod', open: 'never' }]],
  use: {
    baseURL: BASE_URL,
    storageState,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    // A real browser UA + locale; the live OAuth/session path is UA-sensitive.
    locale: 'en-US',
  },
  projects: [
    {
      name: 'desktop',
      testMatch: /audit\.spec\.ts/,
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } },
    },
    {
      // Tablet-width on Chromium (the iPad descriptor defaults to WebKit, which
      // isn't installed here). 768px exercises the same responsive breakpoints.
      name: 'tablet',
      testMatch: /audit\.spec\.ts/,
      use: { ...devices['Desktop Chrome'], viewport: { width: 768, height: 1024 } },
    },
    {
      name: 'mobile',
      testMatch: /audit\.spec\.ts/,
      use: { ...devices['Pixel 5'] }, // 393×851, touch + mobile UA
    },
    {
      // Paid/destructive flows (Generate/Analyze/Chat/render) — desktop only, run
      // explicitly with `--project=flows`, never as part of the default audit.
      name: 'flows',
      testMatch: /flows\.spec\.ts/,
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } },
    },
  ],
})
