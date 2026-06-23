// Per-page screenshot + console smoke harness (Issue 162).
//
// For every SPA route, under both the `desktop` (1440px) and `mobile` (390px)
// projects, this: navigates with the backend mocked, fails on any uncaught JS
// exception or genuine console error (resource-load noise from unmocked media is
// filtered), and writes a full-page screenshot to e2e/__screenshots__/. Those
// screenshots are the artifact for the UX/UI audit — the first time the rendered
// SPA (real CSS, real layout, real dark-mode elevation) is captured in CI-able
// form. jsdom/Vitest cannot produce these (no rendering engine).
//
// Issue 272: three stable, data-free routes (login, pricing, empty-dashboard)
// are promoted to pixel-diff visual regression tests using toHaveScreenshot().
// Baselines live in e2e/__snapshots__/ and MUST be generated on the Linux CI
// runner (ubuntu-latest) via --update-snapshots — WSL2/macOS font anti-aliasing
// differs from the CI runner and causes constant false positives.

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

// ── Visual regression tests (Issue 272) ──────────────────────────────────────
//
// Three stable, data-free routes are promoted to pixel-diff baselines. These
// routes have no dynamic data (login form, pricing table, empty dashboard) —
// a visual diff here means a real CSS/layout regression, not content churn.
//
// CRITICAL: Baselines MUST be generated on the same OS as the CI runner
// (ubuntu-latest / Linux). Run: `npm run test:e2e -- --update-snapshots` in CI
// (or a Linux container), then commit the generated __snapshots__ files.
// Local WSL2/macOS runs will produce false positives due to font-rendering diffs.
//
// Baseline update workflow:
//   1. Push a CI run with `UPDATE_SNAPSHOTS=true` (or manually trigger with
//      `--update-snapshots` in the visual CI job).
//   2. Download the artifact containing the new __snapshots__ PNGs.
//   3. Commit them in a dedicated "chore: update visual baselines" PR.
//
// Dynamic regions to mask: BALANCE fixture has minutes_balance/trial_days_remaining.
// Pricing route is static (no user data), login is fully static, dashboard
// with empty-state fixture has no user-specific numbers beyond what BALANCE returns.
// Mask the balance/trial area on empty-dashboard to prevent fixture-drift failures.

test.describe('visual regression @visual', () => {
  // Login route — fully static, no user data, no dynamic content.
  test.describe('login page', () => {
    test.use({ seed: 'anon' })

    test('login visual baseline', async ({ page }) => {
      await page.goto('login', { waitUntil: 'domcontentloaded' })
      await page
        .getByText('Loading…', { exact: true })
        .waitFor({ state: 'detached', timeout: 10_000 })
        .catch(() => {})
      await page.waitForTimeout(600)

      await expect(page).toHaveScreenshot('login.png', {
        maxDiffPixelRatio: 0.01,
        animations: 'disabled',
      })
    })
  })

  // Pricing route — static layout, no per-user data.
  test('pricing visual baseline', async ({ page }) => {
    await page.goto('pricing', { waitUntil: 'domcontentloaded' })
    await page
      .getByText('Loading…', { exact: true })
      .waitFor({ state: 'detached', timeout: 10_000 })
      .catch(() => {})
    await page.waitForTimeout(600)

    await expect(page).toHaveScreenshot('pricing.png', {
      maxDiffPixelRatio: 0.01,
      animations: 'disabled',
    })
  })

  // Empty dashboard — mask the balance/trial region to prevent fixture-drift
  // failures when the BALANCE fixture changes (minutes_balance, trial_days_remaining).
  // The structural layout (nav, empty-state copy, CTA button) is what we diff.
  test('empty-dashboard visual baseline', async ({ page }) => {
    await page.goto('dashboard', { waitUntil: 'domcontentloaded' })
    await page
      .getByText('Loading…', { exact: true })
      .waitFor({ state: 'detached', timeout: 10_000 })
      .catch(() => {})
    await page.waitForTimeout(600)

    // Mask regions that carry dynamic numbers (balance, trial countdown).
    // These locators are best-effort — if no element matches the mask is a no-op.
    const dynamicRegions = [
      page.locator('[data-testid="balance-display"]'),
      page.locator('[data-testid="trial-countdown"]'),
      page.locator('[aria-label*="balance"]'),
      page.locator('[aria-label*="trial"]'),
    ]

    await expect(page).toHaveScreenshot('empty-dashboard.png', {
      maxDiffPixelRatio: 0.01,
      animations: 'disabled',
      mask: dynamicRegions,
    })
  })
})
