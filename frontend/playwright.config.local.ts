import { existsSync } from 'node:fs'
import { defineConfig, devices } from '@playwright/test'

// LOCAL seeded-backend audit config (Issue 541) — the middle rung between the
// mocked lane (playwright.config.ts, Vite + network mocks) and the live-site
// lane (playwright.config.prod.ts). It points at a locally running uvicorn
// serving the built SPA under /app/, authenticated with a MINTED cc_session
// cookie (scripts/mint_storage_state.py) against the #540 seed database — real
// serializer shapes, real auth-gated rendering, but pre-push and offline.
//
// No webServer block on purpose: the orchestrator (scripts/ui_audit.sh, Issue
// 542) owns migrate → seed → mint → uvicorn boot → run, so this config never
// races it with a second server.
const BASE_URL = 'http://localhost:8000/app/'
export const AUTH_FILE = 'e2e/.auth/local.json'

// storageState must not point at a missing file (Playwright throws). Without a
// minted session the audit still runs — every authed route just lands on login,
// which the auth-proof spec reports loudly instead of 12 cryptic redirects.
const storageState = existsSync(AUTH_FILE) ? AUTH_FILE : undefined

export default defineConfig({
  testDir: './e2e/local',
  outputDir: './e2e/.results/local',
  fullyParallel: true,
  workers: 2,
  retries: 0,
  reporter: [['list'], ['html', { outputFolder: 'e2e/.report-local', open: 'never' }]],
  use: {
    baseURL: BASE_URL,
    storageState,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    locale: 'en-US',
  },
  projects: [
    {
      name: 'desktop',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } },
    },
    {
      name: 'mobile',
      use: { ...devices['Pixel 5'] }, // 393×851, touch + mobile UA
    },
  ],
})
