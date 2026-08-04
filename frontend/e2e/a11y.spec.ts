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
  // Added 2026-08-03 (Issue 389). Long-form source mode gained two independently
  // scrolling regions, and `scrollable-region-focusable` is a SERIOUS-impact rule
  // — a scroll container with no keyboard-reachable content fails it. Short mode
  // alone would not have covered them.
  'editor?video_id=v1',
  // Added 2026-08-04 (Issue 411). The "Soon" preview mocks are aria-hidden now
  // — decorative spans at half opacity have no business in the accessibility
  // tree — which cleared the serious contrast violations that had kept this
  // route out of the gate since 2026-08-03.
  'settings',
]

for (const path of ROUTES) {
  // The editor appears twice (short and long mode), so the bare pathname is no
  // longer a unique test title — Playwright rejects duplicates outright.
  const base = path.split('?')[0]
  const name = base === 'editor' ? (path.includes('clip_id') ? 'editor-short' : 'editor-long') : base
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
