// Post-beta journey specs (Issue 545, Lane L33): triage keyboard flow, GDPR
// export entry, billing checkout entry. Mocked-network lane — deterministic,
// no backend. Checkout never touches Stripe: the POST is intercepted in-spec
// and the "redirect" lands on a routed stub, so the assertion is on the
// request firing and the URL shape only.

import { test, expect } from './fixtures/mock-api'
import { attachCollectors, recordRouteAudit, waitForFonts } from './fixtures/collect'

async function settle(page: import('@playwright/test').Page) {
  await page
    .getByText('Loading…', { exact: true })
    .waitFor({ state: 'detached', timeout: 10_000 })
    .catch(() => {})
  await waitForFonts(page)
}

test('triage keyboard flow: K keeps, the verdict strip offers Undo, Undo retracts', async ({
  page,
}, testInfo) => {
  const issues = attachCollectors(page)
  await page.goto('review?video_id=v1', { waitUntil: 'domcontentloaded' })
  await settle(page)

  // K = Keep (Issue 445 owner decision; the shortcut bus ignores keystrokes
  // aimed at inputs/buttons, so focus the page body first).
  await page.locator('body').click({ position: { x: 5, y: 5 } })
  const feedbackPost = page.waitForRequest(
    (r) => r.method() === 'POST' && /\/clips\/[^/]+\/feedback$/.test(r.url()),
  )
  await page.keyboard.press('k')
  await feedbackPost

  // The page-level verdict strip appears with the Kept verdict and an Undo.
  const strip = page.getByTestId('last-call-strip')
  await expect(strip).toBeVisible()
  await expect(strip.getByText('Kept')).toBeVisible()

  // Undo is a triage retraction (PUT /clips/{id}/triage → pending), never a
  // second feedback write.
  const undoPut = page.waitForRequest(
    (r) => r.method() === 'PUT' && /\/clips\/[^/]+\/triage$/.test(r.url()),
  )
  await strip.getByRole('button', { name: 'Undo' }).click()
  await undoPut

  await recordRouteAudit(page, testInfo, 'mocked', 'triage-keyboard-journey', issues)
  expect(issues.pageErrors, 'uncaught JS during the triage journey').toEqual([])
  expect(issues.consoleErrors, 'console errors during the triage journey').toEqual([])
})

test('GDPR export entry: Settings shows the export section and the request POST fires', async ({
  page,
}, testInfo) => {
  const issues = attachCollectors(page)
  // Model the status probe explicitly: the {} catch-all has no `status` field,
  // which is indistinguishable from 'none' today but would silently mask a
  // shape change. Spec-level route wins over the fixture's catch-all.
  await page.route('**/creators/me/export', (route) => {
    if (route.request().method() === 'GET')
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'none',
          requested_at: null,
          completed_at: null,
          error: null,
        }),
      })
    return route.fulfill({ status: 202, contentType: 'application/json', body: '{}' })
  })

  await page.goto('settings', { waitUntil: 'domcontentloaded' })
  await settle(page)

  await expect(page.getByText('Export your data')).toBeVisible()
  const exportPost = page.waitForRequest(
    (r) => r.method() === 'POST' && r.url().includes('/creators/me/export'),
  )
  await page.getByRole('button', { name: 'Request export' }).click()
  await exportPost

  await recordRouteAudit(page, testInfo, 'mocked', 'gdpr-export-journey', issues)
  expect(issues.pageErrors, 'uncaught JS during the export journey').toEqual([])
  expect(issues.consoleErrors, 'console errors during the export journey').toEqual([])
})

test('billing checkout entry: Buy now POSTs /billing/checkout and follows the session URL', async ({
  page,
}, testInfo) => {
  const issues = attachCollectors(page)

  // Intercept the checkout POST (spec route beats the fixture's benign-200
  // catch-all) and answer with a Stripe-Checkout-shaped session URL; the
  // cross-origin navigation is itself routed to a stub so nothing external
  // is ever contacted. Never a real purchase from the harness.
  const sessionUrl = 'https://checkout.stripe.com/c/pay/cs_test_e2e_545'
  await page.route('**/billing/checkout', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ checkout_url: sessionUrl }),
    }),
  )
  await page.route('https://checkout.stripe.com/**', (route) =>
    route.fulfill({ status: 200, contentType: 'text/html', body: '<title>stub checkout</title>' }),
  )

  await page.goto('pricing', { waitUntil: 'domcontentloaded' })
  await settle(page)

  const checkoutPost = page.waitForRequest(
    (r) => r.method() === 'POST' && r.url().includes('/billing/checkout'),
  )
  await page.getByRole('button', { name: 'Buy now' }).first().click()
  const req = await checkoutPost
  expect(req.postDataJSON()).toHaveProperty('pack_id')

  // The app assigns window.location to the session URL — Checkout-session shape.
  await page.waitForURL(/checkout\.stripe\.com\/c\/pay\/cs_/, { timeout: 10_000 })

  await recordRouteAudit(page, testInfo, 'mocked', 'checkout-entry-journey', issues)
  expect(issues.pageErrors, 'uncaught JS during the checkout journey').toEqual([])
})
