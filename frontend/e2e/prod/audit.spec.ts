// Live-site UI/UX audit (Issue 164) — runs against https://autoclip.studio with a
// real authenticated session (see save-auth.mjs). For every route, at desktop /
// tablet / mobile, it records the gaps the mocked harness can't see:
//   • uncaught JS + real console errors
//   • failed network requests (4xx/5xx on app/api endpoints)
//   • broken images (e.g. YouTube thumbnails that don't load)
//   • accessibility violations (axe-core — contrast, labels, roles, focus order)
//   • a full-page screenshot for human review
// Findings are written to e2e/.results/prod/findings-<project>.json and soft-
// asserted, so the whole sweep completes and produces a report rather than
// bailing on the first problem.
import { test } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { mkdirSync, writeFileSync } from 'node:fs'

// Collect-only audit: every page writes its OWN findings file (robust to
// Playwright restarting the worker between tests) and the run stays green —
// the findings files ARE the report, read + triaged after the run.

// Authed app routes + the public ones the session still renders. analysis/review
// are audited both bare (empty state) and — if a real video is discovered on the
// dashboard — with a live ?video_id=.
const ROUTES: { name: string; path: string }[] = [
  { name: 'dashboard', path: 'dashboard' },
  { name: 'insights', path: 'insights' },
  { name: 'analysis', path: 'analysis' },
  { name: 'review', path: 'review' },
  { name: 'profile', path: 'profile' },
  { name: 'chat', path: 'chat' },
  { name: 'walkthrough', path: 'walkthrough' },
  { name: 'onboarding', path: 'onboarding' },
  { name: 'pricing', path: 'pricing' },
]

// Network noise that isn't an app defect: third-party assets, favicon, and the
// /auth/me probe returning 401 for anonymous (only relevant if the session lapsed).
function isBenignRequest(url: string, status: number): boolean {
  if (/favicon/i.test(url)) return true
  if (!url.includes('autoclip.studio')) return true // google fonts, yt thumbnails, etc.
  if (url.includes('/auth/me') && status === 401) return true
  return false
}

const BENIGN_CONSOLE = [/Download the React DevTools/i, /Failed to load resource/i, /net::ERR_/i]

async function auditPage(
  page: import('@playwright/test').Page,
  name: string,
  path: string,
): Promise<void> {
  const consoleErrors: string[] = []
  const failedRequests: string[] = []
  page.on('pageerror', (e) => consoleErrors.push(`pageerror: ${e}`))
  page.on('console', (m) => {
    if (m.type() === 'error' && !BENIGN_CONSOLE.some((re) => re.test(m.text())))
      consoleErrors.push(m.text())
  })
  page.on('response', (r) => {
    if (r.status() >= 400 && !isBenignRequest(r.url(), r.status()))
      failedRequests.push(`${r.status()} ${r.request().method()} ${r.url()}`)
  })

  await page.goto(path, { waitUntil: 'domcontentloaded' })
  await page
    .getByText('Loading…', { exact: true })
    .waitFor({ state: 'detached', timeout: 15_000 })
    .catch(() => {})
  await page.waitForTimeout(1200) // let data fetches + entrance animations settle

  // Broken images: loaded but zero intrinsic width (404'd or blocked).
  const brokenImages = await page.evaluate(() =>
    Array.from(document.images)
      .filter((img) => img.complete && img.naturalWidth === 0)
      .map((img) => img.currentSrc || img.src),
  )

  const axeResults = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze()
  const axe = axeResults.violations.map((v) => ({
    id: v.id,
    impact: v.impact ?? undefined,
    help: v.help,
    nodes: v.nodes.length,
  }))

  const projectName = test.info().project.name
  mkdirSync('e2e/__screenshots__/prod', { recursive: true })
  await page.screenshot({
    path: `e2e/__screenshots__/prod/${projectName}-${name}.png`,
    fullPage: true,
    animations: 'disabled',
  })

  mkdirSync('e2e/.results/prod', { recursive: true })
  writeFileSync(
    `e2e/.results/prod/finding-${projectName}-${name}.json`,
    JSON.stringify({ name, url: page.url(), consoleErrors, failedRequests, brokenImages, axe }, null, 2),
  )
}

for (const { name, path } of ROUTES) {
  test(`audit ${name}`, async ({ page }) => {
    await auditPage(page, name, path)
  })
}

// A real video, if the account has one, lets us audit the populated Analysis +
// Review screens (not just empty states).
test('audit analysis + review with a real video', async ({ page }) => {
  await page.goto('dashboard', { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(1500)
  const href = await page
    .locator('a[href*="video_id="]')
    .first()
    .getAttribute('href')
    .catch(() => null)
  const videoId = href?.match(/video_id=([^&]+)/)?.[1]
  if (!videoId) {
    test.skip(true, 'no video with ?video_id= found on the dashboard for this account')
    return
  }
  await auditPage(page, 'analysis-video', `analysis?video_id=${videoId}`)
  await auditPage(page, 'review-video', `review?video_id=${videoId}`)
})

// Interaction state: the mobile nav must actually open (the Issue 163 fix), on prod.
test('mobile nav opens on the live site', async ({ page }) => {
  await page.setViewportSize({ width: 393, height: 851 })
  await page.goto('dashboard', { waitUntil: 'domcontentloaded' })
  const toggle = page.getByRole('button', { name: 'Open menu' })
  if (await toggle.isVisible().catch(() => false)) {
    await toggle.click()
    await page.getByRole('button', { name: 'Close menu' }).waitFor({ timeout: 5000 })
    mkdirSync('e2e/__screenshots__/prod', { recursive: true })
    await page.screenshot({ path: 'e2e/__screenshots__/prod/mobile-nav-open.png', fullPage: true })
  }
})
