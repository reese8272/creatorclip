// Fallback auth capture (Issue 164) for when Google refuses to sign in inside the
// automated browser ("this browser may not be secure"). You log in with your NORMAL
// browser, copy the `cc_session` cookie value into e2e/.auth/cc_session.txt, and this
// builds the Playwright storage state the prod audit reuses. No password, no token
// ever touches the chat or the repo (.auth/ is gitignored).
//
//   1) Log into https://autoclip.studio in your normal browser.
//   2) DevTools (F12) → Application → Cookies → https://autoclip.studio → cc_session
//      → copy the Value.
//   3) Paste ONLY that value into frontend/e2e/.auth/cc_session.txt and save.
//   4) node e2e/prod/build-auth-from-cookie.mjs
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'

const COOKIE_FILE = 'e2e/.auth/cc_session.txt'
const AUTH_FILE = 'e2e/.auth/prod.json'

mkdirSync('e2e/.auth', { recursive: true })

let value
try {
  value = readFileSync(COOKIE_FILE, 'utf8').trim()
} catch {
  console.error(`✗ ${COOKIE_FILE} not found. Paste the cc_session value into it first.`)
  process.exit(1)
}
if (!value || value.length < 20) {
  console.error(`✗ ${COOKIE_FILE} looks empty or too short — paste the full cc_session value.`)
  process.exit(1)
}

// Mirror how routers/auth.py sets the cookie: host-only on autoclip.studio, path /,
// httpOnly, secure, SameSite=Lax. expires -1 = session cookie (lives for the run).
const state = {
  cookies: [
    {
      name: 'cc_session',
      value,
      domain: 'autoclip.studio',
      path: '/',
      expires: -1,
      httpOnly: true,
      secure: true,
      sameSite: 'Lax',
    },
  ],
  origins: [],
}

writeFileSync(AUTH_FILE, JSON.stringify(state, null, 2))
console.log(`✓ Session state written → ${AUTH_FILE} (run "npm run test:prod" next)`)
