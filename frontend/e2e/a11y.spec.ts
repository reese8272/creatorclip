// Accessibility regression gate (Issue 165). Runs axe-core against the LOCAL build
// (mocked backend, real CSS) so contrast/role fixes are verified before deploy and
// locked in afterward. The live-site audit (e2e/prod/) found 420 serious
// color-contrast failures; this guards against reintroducing them.
import { test, expect } from './fixtures/mock-api'
import AxeBuilder from '@axe-core/playwright'

const ROUTES = [
  'dashboard',
  'insights',
  'analysis?video_id=v1',
  'review?video_id=v1',
  'profile',
  'chat',
  'walkthrough',
  'onboarding',
  'pricing',
]

for (const path of ROUTES) {
  const name = path.split('?')[0]
  test(`a11y: ${name}`, async ({ page }) => {
    await page.goto(path, { waitUntil: 'domcontentloaded' })
    await page
      .getByText('Loading…', { exact: true })
      .waitFor({ state: 'detached', timeout: 10_000 })
      .catch(() => {})
    await page.waitForTimeout(500)

    const results = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
      .analyze()
    const serious = results.violations.filter(
      (v) => v.impact === 'serious' || v.impact === 'critical',
    )

    // Surface exact failing color pairs in the test output for tuning.
    for (const v of serious) {
      for (const node of v.nodes) {
        const d = node.any?.[0]?.data
        const detail = d?.contrastRatio
          ? ` fg ${d.fgColor} on ${d.bgColor} = ${d.contrastRatio} (need ${d.expectedContrastRatio})`
          : ''
        console.log(`  [${name}] ${v.id}:${detail} — ${node.target?.[0]}`)
      }
    }

    expect(serious, `serious/critical a11y on ${name}`).toEqual([])
  })
}
