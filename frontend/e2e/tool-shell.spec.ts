// Issue 389 — app-shell regression lock-in.
//
// The acceptance criteria are all geometric ("renders at 100vh with no page
// scroll; panels scroll independently"), so they cannot be asserted in jsdom —
// vitest has no layout engine. These run in a real Chromium at 1440×900 (desktop)
// and Pixel 5 (mobile), which is also the only place the mobile-disengages
// behaviour can be checked.

import { expect, test } from './fixtures/mock-api'

const TOOL_ROUTES = [
  { name: 'editor', path: 'editor?video_id=v1&clip_id=c1' },
  { name: 'editor-long', path: 'editor?video_id=v1' },
  { name: 'review', path: 'review?video_id=v1' },
]

const DISCLAIMER = 'AutoClip predicts fit with your style and audience'

async function openTool(page: import('@playwright/test').Page, path: string): Promise<void> {
  await page.goto(path, { waitUntil: 'domcontentloaded' })
  await page.getByText('Loading…', { exact: true }).waitFor({ state: 'detached', timeout: 10_000 })
  // Let react-query settle the clip/transcript fetches before measuring.
  await page.waitForTimeout(700)
}

for (const route of TOOL_ROUTES) {
  test.describe(route.name, () => {
    test('the shell fills the viewport and the page never scrolls', async ({ page }, testInfo) => {
      test.skip(testInfo.project.name !== 'desktop', 'shell engages at lg and up')
      await openTool(page, route.path)

      const m = await page.evaluate(() => {
        const se = document.scrollingElement as HTMLElement
        const chrome = document.querySelector('[data-tool-chrome]') as HTMLElement
        return {
          overflow: se.scrollHeight - se.clientHeight,
          chromeH: Math.round(chrome.getBoundingClientRect().height),
          innerH: window.innerHeight,
        }
      })
      expect(m.overflow).toBeLessThanOrEqual(1)
      expect(m.chromeH).toBe(m.innerH)

      // A page that cannot scroll must actually refuse to.
      await page.evaluate(() => window.scrollBy(0, 500))
      expect(await page.evaluate(() => window.scrollY)).toBe(0)
    })

    test('panels scroll independently and the chrome stays put', async ({ page }, testInfo) => {
      test.skip(testInfo.project.name !== 'desktop', 'shell engages at lg and up')
      await openTool(page, route.path)

      const regions = page.locator('[data-tool-scroll]')
      expect(await regions.count()).toBeGreaterThanOrEqual(2)

      // Every region must really be a scroll container, not just marked as one.
      const overflows = await regions.evaluateAll((els) =>
        els.map((el) => getComputedStyle(el).overflowY),
      )
      for (const o of overflows) expect(['auto', 'scroll']).toContain(o)

      // Scroll the tallest region and prove the page did not move with it. This is
      // the actual complaint the issue opens with: "the timeline can leave the
      // viewport while you are editing against it".
      const statusBar = page.locator('footer').first()
      const before = await statusBar.boundingBox()
      const scrolled = await regions.evaluateAll((els) => {
        const target = els
          .map((el) => el as HTMLElement)
          .sort((a, b) => b.scrollHeight - b.clientHeight - (a.scrollHeight - a.clientHeight))[0]
        if (!target || target.scrollHeight <= target.clientHeight) return null
        target.scrollTop = 400
        return target.scrollTop
      })
      if (scrolled !== null) expect(scrolled).toBeGreaterThan(0)
      expect(await page.evaluate(() => window.scrollY)).toBe(0)
      expect(await statusBar.boundingBox()).toEqual(before)
    })

    test('the honesty statement and legal links stay visible, with no marketing footer', async ({
      page,
    }) => {
      await openTool(page, route.path)

      // CLAUDE.md hard constraint — visible without scrolling, on every tool route
      // and on both projects (docked at the bottom on desktop, at the top on mobile).
      await expect(page.getByText(DISCLAIMER, { exact: false })).toBeInViewport()

      // Exactly one legal-link set: two would mean the marketing footer came back,
      // zero would mean the tool routes dropped a Google-verification requirement.
      await expect(page.getByRole('link', { name: 'Terms' })).toHaveCount(1)
      await expect(page.getByRole('link', { name: 'Accessibility' })).toHaveCount(1)
      await expect(page.locator('main')).toHaveCount(1)
    })

    test('mobile keeps a stacked, scrolling layout with no horizontal scroll', async ({
      page,
    }, testInfo) => {
      test.skip(testInfo.project.name !== 'mobile', 'mobile-only behaviour')
      await openTool(page, route.path)

      const m = await page.evaluate(() => {
        const se = document.scrollingElement as HTMLElement
        const chrome = document.querySelector('[data-tool-chrome]') as HTMLElement
        const main = document.querySelector('main') as HTMLElement
        return {
          hOverflow: se.scrollWidth - window.innerWidth,
          chromeOverflow: getComputedStyle(chrome).overflow,
          mainOverflow: getComputedStyle(main).overflowY,
        }
      })
      expect(m.hOverflow).toBeLessThanOrEqual(1)
      // The shell must DISENGAGE below lg: nothing clips, so the document scrolls
      // and the layout stacks. Individual panels may still keep their own
      // viewport-relative caps — that is the pre-389 mobile behaviour, preserved.
      expect(m.chromeOverflow).toBe('visible')
      expect(m.mainOverflow).toBe('visible')
    })
  })
}

test('the editor player is sized to its importance, with no dead region', async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'measured at 1440x900')
  await openTool(page, 'editor?video_id=v1&clip_id=c1')

  const m = await page.evaluate(() => {
    const card = document.querySelector('section[aria-label="Clip preview"] > *') as HTMLElement
    const video = document.querySelector('video') as HTMLElement
    const section = document.querySelector('section[aria-label="Clip preview"]') as HTMLElement
    return {
      videoW: video.getBoundingClientRect().width,
      cardW: card.getBoundingClientRect().width,
      cardH: card.getBoundingClientRect().height,
      sectionH: section.getBoundingClientRect().height,
    }
  })

  // Was a frozen 180px before Issue 389.
  expect(m.videoW).toBeGreaterThan(240)
  // A w-fit card on an auto track cannot frame empty space beside the player.
  expect(m.cardW - m.videoW).toBeLessThan(40)
  // And the card must FIT its row — overflowing it hides the meta below the fold,
  // which is exactly what an under-measured chrome allowance caused during build.
  expect(m.cardH).toBeLessThanOrEqual(m.sectionH)
})
