// Issue 409 — the persistence loop, end to end, in a real browser.
//
// Every other editor spec exercises LAYOUT; before this file, none exercised
// the edit document itself — the mock had no /edit-document route, so the
// editor hydrated from the catch-all `{}` and the entire autosave surface ran
// unobserved. This spec pins the loop: hydrate a STORED document (not the
// empty default), commit an edit through a real control, and watch the
// debounced autosave land as a visible "Saved".

import { expect, test } from './fixtures/mock-api'

test.use({
  editDocSeed: {
    revision: 3,
    doc: {
      version: 1,
      cuts: [{ id: 'seed-cut', start_s: 5, end_s: 9 }],
      last_applied_at: null,
    },
  },
})

test('hydrates the stored document and lands an autosave as Saved', async ({
  page,
  unmatchedGets,
}) => {
  await page.goto('editor?video_id=v1&clip_id=c1', { waitUntil: 'domcontentloaded' })
  await page.getByText('Loading…', { exact: true }).waitFor({ state: 'detached', timeout: 10_000 })

  // Hydration: the stored cut is present, not an empty editor.
  await expect(page.getByText(/^1 cut\(s\)/)).toBeVisible()

  // Commit through a real control. The debounced autosave PUTs against the
  // mock's compare-and-set (base 3 → revision 4) and must surface as Saved.
  await page.getByRole('button', { name: 'Clear all' }).click()
  await expect(page.getByText(/^0 cut\(s\)/)).toBeVisible()
  await expect(page.getByText('Saved', { exact: true })).toBeVisible({ timeout: 5_000 })

  // The flow really went through the modelled route — nothing about the edit
  // document fell through to the `{}` catch-all.
  expect(unmatchedGets.filter((p) => p.includes('edit-document'))).toEqual([])
})
