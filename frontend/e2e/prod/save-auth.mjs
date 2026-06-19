// One-time auth capture for the live-site audit (Issue 164).
//
// Opens a headed browser at the AutoClip login page, waits for you to complete
// Google sign-in, then saves the authenticated storage state (the signed
// `cc_session` cookie) to e2e/.auth/prod.json. The prod Playwright config reuses
// that file so the audit never has to log in again.
//
//   node e2e/prod/save-auth.mjs
//
// Notes:
// - Needs a display. In WSL2 that means WSLg (DISPLAY set) — already present here.
// - Google may reject automation-flavored browsers ("this browser may not be
//   secure"). We launch the real Chrome channel first for that reason; if Chrome
//   isn't installed we fall back to bundled Chromium. If Google still blocks the
//   login, use the manual-cookie fallback documented in the audit README.
import { chromium } from '@playwright/test'
import { mkdirSync } from 'node:fs'

const LOGIN_URL = 'https://autoclip.studio/app/login'
const AUTH_FILE = 'e2e/.auth/prod.json'
const TIMEOUT_MS = 4 * 60 * 1000

mkdirSync('e2e/.auth', { recursive: true })

async function launch() {
  try {
    return await chromium.launch({ headless: false, channel: 'chrome' })
  } catch {
    console.log('(real Chrome not found — falling back to bundled Chromium)')
    return await chromium.launch({ headless: false })
  }
}

const browser = await launch()
const context = await browser.newContext({ locale: 'en-US' })
const page = await context.newPage()

await page.goto(LOGIN_URL)
console.log('\n→ Complete the Google sign-in in the opened window.')
console.log('  Waiting for you to land back in the app (up to 4 min)…\n')

try {
  // OAuth bounces to accounts.google.com and back; success = any /app/* page
  // that isn't the login screen (dashboard, onboarding, or walkthrough).
  await page.waitForURL(
    (url) => url.pathname.startsWith('/app') && !url.pathname.endsWith('/login'),
    { timeout: TIMEOUT_MS },
  )
  await context.storageState({ path: AUTH_FILE })
  console.log(`✓ Session captured → ${AUTH_FILE}`)
} catch {
  console.error('✗ Did not detect a successful login within the timeout.')
  console.error('  If Google blocked the browser, use the manual-cookie fallback.')
  process.exitCode = 1
} finally {
  await browser.close()
}
