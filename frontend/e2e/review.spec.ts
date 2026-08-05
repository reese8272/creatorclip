import { test, expect } from './fixtures/mock-api'

// Issue 388 — the geometric half of the action-row regression test.
//
// The defect was a LAYOUT bug: two inline-level <button>s as bare siblings in a
// non-flex container rendered butted together as one run-on string ("Suggest
// titles / rewrite hookSuggest caption / overlay text"), with their mt-2/mt-1
// doing nothing because vertical margin has no effect on inline boxes.
//
// jsdom cannot see this — it has no layout engine, and vite.config sets
// `css: false`, so Tailwind classes are never even applied. The unit test in
// WhyThisClip.test.tsx therefore asserts the flex/gap structure; this asserts
// the thing the user actually experiences: measurable space between the two
// click targets, in a real browser with real CSS.
test('the two clip suggestion actions are visibly separated', async ({ page }) => {
  await page.goto('review?video_id=v1', { waitUntil: 'domcontentloaded' })

  // Issue 424 moved the lazy suggestion triggers into the metadata panel's
  // "More suggestions" disclosure, relabeled Regenerate. Open it first.
  await page.getByText('More suggestions').click()

  const titles = page.getByRole('button', { name: /Regenerate titles/i })
  const caption = page.getByRole('button', { name: /Regenerate caption/i })
  await titles.waitFor()

  const a = await titles.boundingBox()
  const b = await caption.boundingBox()
  expect(a, 'titles trigger should be laid out').not.toBeNull()
  expect(b, 'caption trigger should be laid out').not.toBeNull()

  // Either they sit side by side with a horizontal gap, or they wrapped onto
  // separate rows with a vertical one. Both are fine; touching is not.
  const horizontal = b!.x - (a!.x + a!.width)
  const vertical = b!.y - (a!.y + a!.height)
  expect(
    Math.max(horizontal, vertical),
    `actions are touching — horizontal gap ${horizontal}px, vertical gap ${vertical}px`,
  ).toBeGreaterThanOrEqual(4)
})

// L26 Issue 424 — the sizing inversion's acceptance gate. The pre-L26 media box
// measured 273×486 at 1440×900 (~9% of the workspace); the stage flip must buy
// at least 1.8× that area. Geometry, not pixels — this runs anywhere, no
// baseline regen involved.
test('the review media box is at least 1.8x the pre-L26 area at 1440x900', async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'measured at 1440x900')
  await page.goto('review?video_id=v1', { waitUntil: 'domcontentloaded' })

  const video = page.locator('section[aria-label="Clip preview"] video')
  await video.waitFor()
  const box = await video.boundingBox()
  expect(box, 'the stage player should be laid out').not.toBeNull()

  const PRE_L26_AREA = 273 * 486
  expect(
    box!.width * box!.height,
    `media box ${Math.round(box!.width)}×${Math.round(box!.height)} is under 1.8× the old ${273}×${486}`,
  ).toBeGreaterThanOrEqual(1.8 * PRE_L26_AREA)
})

// L26 Issue 426 — the crop-track overlay. c1 404s (`no_crop_track`, the normal
// pre-pipeline state) and must show NOTHING; c2 is the one tracked clip and
// shows the honest mini-map, which may never intercept pointers.
test('the crop-track mini-map shows on the tracked clip and never on the 404 clip', async ({
  page,
}) => {
  await page.goto('review?video_id=v1', { waitUntil: 'domcontentloaded' })
  await page.locator('section[aria-label="Clip preview"] video').waitFor()

  // c1: the 404 clip — give the query time to settle, then assert absence.
  await page.waitForTimeout(600)
  await expect(page.getByTestId('crop-track-overlay')).toHaveCount(0)

  // Advance to c2, the tracked clip.
  await page.getByRole('button', { name: /Next clip/ }).click()
  const overlay = page.getByTestId('crop-track-overlay')
  await overlay.waitFor()
  await expect(overlay).toContainText('AI framing')
  expect(await overlay.evaluate((el) => getComputedStyle(el).pointerEvents)).toBe('none')
  // One tick for the fixture's speaker-change cut.
  await expect(page.getByTestId('crop-cut-tick')).toHaveCount(1)
})
