// Recap-surface journey (Issue 544, Lane L33). The recap bundle is the beta's
// second deliverable (Lane L32) and until this spec had only vitest coverage —
// smoke.spec proves the route renders; this proves the JOURNEY: the newest
// summary's segments render in story order with their cited principles, the
// honesty copy is present, and the surface stays error-free on both viewports.
//
// Data comes from the mock-api SUMMARIES fixture (sum1: ready/done, 2 segments
// citing "Open Loop" and "Pattern Interrupt").

import { test, expect } from './fixtures/mock-api'
import { attachCollectors, recordRouteAudit, waitForFonts } from './fixtures/collect'

test('recap surface renders the summary bundle', async ({ page }, testInfo) => {
  const issues = attachCollectors(page)

  await page.goto('video/v1/recap', { waitUntil: 'domcontentloaded' })
  await page
    .getByText('Loading…', { exact: true })
    .waitFor({ state: 'detached', timeout: 10_000 })
    .catch(() => {})
  await waitForFonts(page)

  // The segment list renders in story order with cited principles.
  await expect(page.getByText('Segments — in story order')).toBeVisible()
  await expect(page.getByText('Open Loop')).toBeVisible()
  await expect(page.getByText('Pattern Interrupt')).toBeVisible()

  // Honesty constraint copy — estimates, never promises (structural product rule).
  await expect(page.getByText(/estimates grounded in your own channel data/i)).toBeVisible()

  // Segments render chronologically: "Open Loop" (30s) before "Pattern Interrupt" (240s).
  const openLoop = await page.getByText('Open Loop').boundingBox()
  const patternInterrupt = await page.getByText('Pattern Interrupt').boundingBox()
  expect(openLoop && patternInterrupt && openLoop.y < patternInterrupt.y).toBe(true)

  await recordRouteAudit(page, testInfo, 'mocked', 'recap-journey', issues)
  expect(issues.pageErrors, 'uncaught JS on the recap surface').toEqual([])
  expect(issues.consoleErrors, 'console errors on the recap surface').toEqual([])
})
