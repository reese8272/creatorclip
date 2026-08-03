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

  const titles = page.getByRole('button', { name: /Suggest titles/i })
  const caption = page.getByRole('button', { name: /Suggest caption/i })
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
