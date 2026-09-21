// Uppy upload journey (Issue 544, Lane L33). The upload panel is the first
// thing a new user touches (Issue 395) and had no browser-level coverage: this
// drives a real file through the input with the network mocked in proxy mode
// (mock-api's /videos/uploads/config forces the same-origin legacy endpoint,
// so no presign/R2 traffic ever fires) and asserts the queue UI advances to
// the completion state without errors.

import { test, expect } from './fixtures/mock-api'
import { attachCollectors, recordRouteAudit, waitForFonts } from './fixtures/collect'

test('uploading a file advances the queue to the done state', async ({ page }, testInfo) => {
  const issues = attachCollectors(page)

  await page.goto('dashboard', { waitUntil: 'domcontentloaded' })
  await page
    .getByText('Loading…', { exact: true })
    .waitFor({ state: 'detached', timeout: 10_000 })
    .catch(() => {})
  await waitForFonts(page)

  // The form is collapsed behind the secondary "+ Upload a video" toggle
  // (Issue 355: Upload must not compete with the page's primary action).
  await page.getByRole('button', { name: '+ Upload a video' }).click()

  const input = page.getByLabel('Video files to upload')
  await input.setInputFiles({
    name: 'tiny-upload.mp4',
    mimeType: 'video/mp4',
    buffer: Buffer.from('not-a-real-mp4-but-the-proxy-mock-accepts-anything'),
  })

  // The queue row appears "Ready"; the upload starts on the explicit Upload
  // click and reaches the done label — "Uploaded — analysing now."
  // (UploadVideoForm STATUS_LABEL). The mocked proxy POST returns 200
  // immediately, so this is fast; the assertion is on the UI state machine,
  // not network timing.
  await expect(page.getByText('tiny-upload.mp4')).toBeVisible({ timeout: 10_000 })
  await expect(page.getByText('Ready', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Upload', exact: true }).click()
  await expect(page.getByText('Uploaded — analysing now.')).toBeVisible({ timeout: 15_000 })

  await recordRouteAudit(page, testInfo, 'mocked', 'upload-journey', issues)
  expect(issues.pageErrors, 'uncaught JS during the upload journey').toEqual([])
  expect(issues.consoleErrors, 'console errors during the upload journey').toEqual([])
})
