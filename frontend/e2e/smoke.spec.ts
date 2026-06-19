// Per-page screenshot + console smoke harness (Issue 162).
//
// For every SPA route, under both the `desktop` (1440px) and `mobile` (390px)
// projects, this: navigates with the backend mocked, fails on any uncaught JS
// exception or genuine console error (resource-load noise from unmocked media is
// filtered), and writes a full-page screenshot to e2e/__screenshots__/. Those
// screenshots are the artifact for the UX/UI audit — the first time the rendered
// SPA (real CSS, real layout, real dark-mode elevation) is captured in CI-able
// form. jsdom/Vitest cannot produce these (no rendering engine).

import { test, expect } from './fixtures/mock-api'

// Authed pages (rendered behind AuthGate). Analysis/Review need ?video_id=.
const AUTHED_PAGES = [
  { name: 'dashboard', path: 'dashboard' },
  { name: 'insights', path: 'insights' },
  { name: 'analysis', path: 'analysis?video_id=v1' },
  { name: 'review', path: 'review?video_id=v1' },
  { name: 'profile', path: 'profile' },
  { name: 'chat', path: 'chat' },
  { name: 'onboarding', path: 'onboarding' },
  { name: 'walkthrough', path: 'walkthrough' },
  { name: 'pricing', path: 'pricing' }, // public, but renders fine when authed
]

// Console errors we treat as noise: unmocked media/image loads (clip render_uri
// is null in fixtures; YouTube thumbnails hit the network). These are network
// resource failures, not app bugs — the signal we care about is pageerror
// (uncaught JS) and real console.error from React.
const BENIGN = [
  /Failed to load resource/i,
  /net::ERR_/i,
  /favicon/i,
  /Download the React DevTools/i,
]

function isBenign(text: string): boolean {
  return BENIGN.some((re) => re.test(text))
}

async function capturePage(
  page: import('@playwright/test').Page,
  testInfo: import('@playwright/test').TestInfo,
  name: string,
  path: string,
): Promise<void> {
  const pageErrors: string[] = []
  const consoleErrors: string[] = []
  page.on('pageerror', (err) => pageErrors.push(String(err)))
  page.on('console', (msg) => {
    if (msg.type() === 'error' && !isBenign(msg.text())) consoleErrors.push(msg.text())
  })

  await page.goto(path, { waitUntil: 'domcontentloaded' })
  // AuthGate shows "Loading…" until the /auth/me mock resolves; wait it out.
  await page
    .getByText('Loading…', { exact: true })
    .waitFor({ state: 'detached', timeout: 10_000 })
    .catch(() => {})
  await page.waitForTimeout(600) // let entrance animations + fonts settle

  // Playwright runs with cwd at the config dir (frontend/), so this lands in
  // frontend/e2e/__screenshots__/ regardless of which spec invokes it.
  const project = testInfo.project.name
  await page.screenshot({
    path: `e2e/__screenshots__/${project}-${name}.png`,
    fullPage: true,
    animations: 'disabled',
  })

  expect(pageErrors, `uncaught JS exceptions on /${name}`).toEqual([])
  expect(consoleErrors, `console errors on /${name}`).toEqual([])
}

for (const { name, path } of AUTHED_PAGES) {
  test(`renders ${name}`, async ({ page }, testInfo) => {
    await capturePage(page, testInfo, name, path)
  })
}

test.describe('logged out', () => {
  test.use({ seed: 'anon' })

  test('renders login', async ({ page }, testInfo) => {
    await capturePage(page, testInfo, 'login', 'login')
  })
})
