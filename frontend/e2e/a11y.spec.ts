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
  // Added 2026-08-03 (Issue 386). The editor is the most widget-dense route in
  // the app — tablist, role="textbox", canvas timeline, custom media player —
  // and was the only unaudited one that #386 and #400b both rewrite. A baseline
  // spike on the pre-#386 tree confirmed it passes clean, so anything that
  // appears here is ours.
  'editor?video_id=v1&clip_id=c1',
  // NOT added: `settings`. It has PRE-EXISTING serious contrast violations from
  // the "Soon" preview rows (commit 80a7474, 2026-06-23) — decorative disabled
  // mocks wrapped in `pointer-events-none opacity-50`, which halves their
  // effective contrast while leaving them in the accessibility tree. Logged in
  // docs/OFF_COURSE_BUGS.md; adding the route before fixing them would block
  // this batch on unrelated work.
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
