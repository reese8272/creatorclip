// Local seeded-backend UI audit (Issue 541) — mirrors e2e/prod/audit.spec.ts,
// but against a locally running uvicorn + the #540 seed database, authenticated
// with a minted cc_session cookie (scripts/mint_storage_state.py). Per route ×
// viewport it records what the mocked lane can't see: real serializer shapes
// breaking a render, uncaught JS, failed API requests, axe violations, plus a
// full-page screenshot for vision review.
//
// No Celery worker runs in this lane: anything asynchronous the seed leaves
// behind must read as queued/pending in the UI — never assert a job as done.
//
// NOTE (Issue 542): the console/pageerror/failed-request collection below stays
// inline for now; a shared e2e/fixtures/collect.ts fixture (NDJSON events for
// the merged report) arrives with the ui_audit.sh orchestrator and this spec
// will switch to it then.
import { expect, test } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { mkdirSync, writeFileSync } from 'node:fs'

// The #540 seed creator — must match scripts/mint_storage_state.py's default.
const SEED_CREATOR_ID = '00000000-1111-2222-3333-444444444444'

const SCREEN_DIR = 'e2e/.audit/screens/local'
const FINDINGS_DIR = 'e2e/.results/local'

// Every authed SPA route from frontend/src/App.tsx (AppChrome + ToolChrome +
// bare flows), plus pricing (public but rendered inside the session). The
// id-carrying routes (video/:id, video/:id/recap) are audited separately below
// after discovering a seeded video id through the real API.
const ROUTES: { name: string; path: string }[] = [
  { name: 'dashboard', path: 'dashboard' },
  { name: 'insights', path: 'insights' },
  { name: 'analysis', path: 'analysis' },
  { name: 'review', path: 'review' },
  { name: 'editor', path: 'editor' },
  { name: 'settings', path: 'settings' },
  { name: 'profile', path: 'profile' },
  { name: 'chat', path: 'chat' },
  { name: 'walkthrough', path: 'walkthrough' },
  { name: 'onboarding', path: 'onboarding' },
  { name: 'pricing', path: 'pricing' },
]

// Network noise that isn't an app defect on the local rung: favicon probes and
// anything not served by the local backend (fonts, YouTube thumbnails).
function isBenignRequest(url: string, status: number): boolean {
  if (/favicon/i.test(url)) return true
  if (!url.includes('localhost:8000')) return true
  void status
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

  const viewport = test.info().project.name
  mkdirSync(SCREEN_DIR, { recursive: true })
  await page.screenshot({
    path: `${SCREEN_DIR}/${name}-${viewport}.png`,
    fullPage: true,
    animations: 'disabled',
  })

  mkdirSync(FINDINGS_DIR, { recursive: true })
  writeFileSync(
    `${FINDINGS_DIR}/finding-${viewport}-${name}.json`,
    JSON.stringify(
      { name, url: page.url(), consoleErrors, failedRequests, brokenImages, axe },
      null,
      2,
    ),
  )
}

// Proof the minted cookie actually authenticates: a real GET /creators/me from
// the browser context must return the seeded creator — no dependency overrides
// anywhere. If this fails, every routed audit below is auditing the login page.
test('minted cc_session authenticates against /creators/me', async ({ page }) => {
  const res = await page.request.get('/creators/me')
  expect(res.status(), 'minted cookie rejected — re-run scripts/mint_storage_state.py').toBe(200)
  const body = (await res.json()) as { id: string }
  expect(body.id).toBe(SEED_CREATOR_ID)
})

for (const { name, path } of ROUTES) {
  test(`audit ${name}`, async ({ page }) => {
    await auditPage(page, name, path)
  })
}

// Id-carrying routes: discover a seeded video through the real API (same
// APIRequestContext, same minted cookie) rather than hardcoding an id the seed
// might regenerate.
test('audit video detail + recap with a seeded video', async ({ page }) => {
  const res = await page.request.get('/videos')
  expect(res.status()).toBe(200)
  const body = (await res.json()) as { videos: { id: string }[] }
  const videoId = body.videos[0]?.id
  if (!videoId) {
    test.skip(true, 'seed has no videos — run scripts/seed_staging.py --profile ui (#540) first')
    return
  }
  await auditPage(page, 'video-detail', `video/${videoId}`)
  await auditPage(page, 'video-recap', `video/${videoId}/recap`)
})
