// PAID / DESTRUCTIVE live flows (Issue 164) — runs ONLY via `npm run test:prod:flows`
// (project=flows). Each test triggers a real backend action that spends LLM tokens
// and/or minutes and WRITES to the signed-in account (chat history, analyses). Keep
// it to one invocation per flow. The heaviest action (clip render — consumes minutes
// + long async job) is gated behind RUN_RENDER=1 and skipped by default.
//
// The point is to prove the full stack works on prod: the live endpoint accepts the
// request and returns 200, and the UI renders the result. We assert on the network
// response (deterministic) and screenshot the result (for the eye).
import { test, expect } from '@playwright/test'
import { mkdirSync } from 'node:fs'

mkdirSync('e2e/__screenshots__/prod', { recursive: true })
const shot = (name: string) => `e2e/__screenshots__/prod/flow-${name}.png`

// Discover a real video id from the dashboard so the per-video flows hit real data.
async function discoverVideoId(page: import('@playwright/test').Page): Promise<string | null> {
  await page.goto('dashboard', { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(1500)
  const href = await page
    .locator('a[href*="video_id="]')
    .first()
    .getAttribute('href')
    .catch(() => null)
  return href?.match(/video_id=([^&]+)/)?.[1] ?? null
}

test('flow: channel chat (spends LLM tokens, writes a conversation)', async ({ page }) => {
  await page.goto('chat', { waitUntil: 'domcontentloaded' })
  await page.getByPlaceholder('Ask about your channel…').fill('What were my best videos this month?')
  const resp = page.waitForResponse(
    (r) => r.url().includes('/api/chat/messages') && r.request().method() === 'POST',
  )
  await page.getByRole('button', { name: 'Send' }).click()
  expect.soft((await resp).status(), 'chat endpoint should accept the message').toBeLessThan(400)
  // Let the SSE stream produce at least the start of an answer, then capture.
  await page.waitForTimeout(8000)
  await page.screenshot({ path: shot('chat'), fullPage: true })
})

test('flow: video analysis (spends LLM tokens)', async ({ page }) => {
  // Issue 274 (OCB-3): assert on HTTP response status rather than rendered output.
  // The endpoint can take >60s for LLM generation — asserting on the 200 response
  // header is deterministic and catches endpoint failures without coupling to render
  // timing. The screenshot captures whatever the UI shows at that point.
  const videoId = await discoverVideoId(page)
  await page.goto('analysis', { waitUntil: 'domcontentloaded' })
  await page.getByPlaceholder(/youtu\.be/).fill(videoId ?? 'dQw4w9WgXcQ')
  await page.getByPlaceholder(/underperform/).fill('What made this video perform the way it did?')
  const respPromise = page.waitForResponse((r) => r.url().includes('/video-analysis'), { timeout: 120_000 })
  await page.getByRole('button', { name: /Analyze/ }).click()
  const resp = await respPromise
  expect.soft(resp.status(), 'analysis endpoint should return 200').toBe(200)
  // Brief wait for the UI to begin rendering (not full LLM generation).
  await page.waitForTimeout(3000)
  await page.screenshot({ path: shot('analysis'), fullPage: true })
})

test('flow: title optimizer (spends LLM tokens)', async ({ page }) => {
  // Issue 274 (OCB-3): assert on HTTP 200, not rendered content — same rationale as above.
  const videoId = await discoverVideoId(page)
  test.skip(!videoId, 'no video available on this account to optimize titles for')
  await page.goto(`analysis?video_id=${videoId}`, { waitUntil: 'domcontentloaded' })
  const respPromise = page.waitForResponse((r) => r.url().includes('/titles'), { timeout: 120_000 })
  await page.getByRole('button', { name: 'Generate titles' }).click()
  const resp = await respPromise
  expect.soft(resp.status(), 'titles endpoint should return 200').toBe(200)
  await page.waitForTimeout(3000)
  await page.screenshot({ path: shot('titles'), fullPage: true })
})

test('flow: clip render (consumes minutes — gated)', async ({ page }) => {
  test.skip(
    process.env.RUN_RENDER !== '1',
    'clip render consumes trial minutes + a long async job — set RUN_RENDER=1 to include it',
  )
  // Intentionally minimal: rendering is the most expensive action and its UI path
  // (generate clips → review → render) needs mapping before we spend minutes on it.
  // Left as an explicit opt-in so it is never triggered by accident.
  await page.goto('review', { waitUntil: 'domcontentloaded' })
  await page.screenshot({ path: shot('review-prerender'), fullPage: true })
})
