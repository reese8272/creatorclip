import { defineConfig, devices } from '@playwright/test'

// E2E + visual harness for the React SPA (Issue 162). Runs a real Chromium
// against the Vite dev server, with the FastAPI backend mocked at the network
// boundary (see e2e/fixtures/mock-api.ts) so every page renders deterministically
// without Postgres/Redis/OAuth — honoring the documented no-Docker dev constraint.
//
// The SPA serves under /app/ (Vite `base` + FastAPI fallback), so baseURL carries
// the prefix and specs navigate with bare relative paths ('dashboard', 'login').
const PORT = 5173
const BASE_URL = `http://localhost:${PORT}/app/`

export default defineConfig({
  testDir: './e2e',
  outputDir: './e2e/.results',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ['html', { outputFolder: 'e2e/.report', open: 'never' }]],
  use: {
    baseURL: BASE_URL,
    screenshot: 'only-on-failure',
    trace: 'on-first-retry',
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
  webServer: {
    command: 'npm run dev',
    url: BASE_URL,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
})
