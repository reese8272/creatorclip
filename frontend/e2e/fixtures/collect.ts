// Shared per-route audit collection (Issue 542, Lane L33).
//
// One engine for every lane (mocked / local / prod): attach console, pageerror
// and failed-request listeners with the house benign-noise filter, capture a
// screenshot at a flat predictable path, and append one NDJSON line per
// route×viewport to e2e/.audit/events.ndjson. merge-report.mjs folds the
// NDJSON plus Playwright's JSON reporter output into e2e/.audit/report.json —
// the machine-readable artifact a vision-capable agent reads (see the
// /ui-check skill).
//
// Playwright runs with cwd at the config dir (frontend/), so all relative
// paths below land under frontend/e2e/.audit/ regardless of the invoking spec.

import * as fs from 'node:fs'
import * as path from 'node:path'
import type { Page, TestInfo } from '@playwright/test'

export const AUDIT_DIR = path.resolve('e2e/.audit')

// Console errors treated as noise (extracted verbatim from smoke.spec.ts):
// unmocked media/image loads are network resource failures, not app bugs —
// the signal is pageerror (uncaught JS) and real console.error from React.
const BENIGN = [
  /Failed to load resource/i,
  /net::ERR_/i,
  /favicon/i,
  /Download the React DevTools/i,
]

export function isBenign(text: string): boolean {
  return BENIGN.some((re) => re.test(text))
}

export interface RouteIssues {
  consoleErrors: string[]
  pageErrors: string[]
  failedRequests: string[]
}

/** Attach listeners BEFORE navigation; returns live arrays that fill as the
 * page runs. Failed requests are recorded even when benign-filtered from
 * consoleErrors — the report wants them, the assertion does not. */
export function attachCollectors(page: Page): RouteIssues {
  const issues: RouteIssues = { consoleErrors: [], pageErrors: [], failedRequests: [] }
  page.on('pageerror', (err) => issues.pageErrors.push(String(err)))
  page.on('console', (msg) => {
    if (msg.type() === 'error' && !isBenign(msg.text())) issues.consoleErrors.push(msg.text())
  })
  page.on('requestfailed', (req) => {
    issues.failedRequests.push(`${req.method()} ${req.url()} — ${req.failure()?.errorText ?? '?'}`)
  })
  page.on('response', (res) => {
    if (res.status() >= 500) issues.failedRequests.push(`${res.request().method()} ${res.url()} — HTTP ${res.status()}`)
  })
  return issues
}

/** Force the app's two font families before capture (Issue 413: a fixed settle
 * raced webfont loading and baselines shipped fallback-font renders). */
export async function waitForFonts(page: Page): Promise<void> {
  await page.evaluate(async () => {
    await Promise.all([
      document.fonts.load('400 1rem "Lexend Variable"'),
      document.fonts.load('400 1rem "Geist Mono Variable"'),
    ])
    await document.fonts.ready
  })
}

export interface AuditEvent {
  rung: string
  route: string
  viewport: string
  screenshot: string
  consoleErrors: string[]
  pageErrors: string[]
  failedRequests: string[]
  axeViolations?: unknown[]
}

/** Screenshot to the flat agent-enumerable path and append the NDJSON event.
 * Returns the event so specs can still assert on the collected issues. */
export async function recordRouteAudit(
  page: Page,
  testInfo: TestInfo,
  rung: string,
  route: string,
  issues: RouteIssues,
  axeViolations?: unknown[],
): Promise<AuditEvent> {
  const viewport = testInfo.project.name
  const screensDir = path.join(AUDIT_DIR, 'screens', rung)
  fs.mkdirSync(screensDir, { recursive: true })
  const screenshot = path.join(screensDir, `${route}-${viewport}.png`)
  await page.screenshot({ path: screenshot, fullPage: true, animations: 'disabled' })

  const event: AuditEvent = {
    rung,
    route,
    viewport,
    screenshot: path.relative(path.resolve('.'), screenshot),
    consoleErrors: issues.consoleErrors,
    pageErrors: issues.pageErrors,
    failedRequests: issues.failedRequests,
    ...(axeViolations !== undefined ? { axeViolations } : {}),
  }
  fs.mkdirSync(AUDIT_DIR, { recursive: true })
  fs.appendFileSync(path.join(AUDIT_DIR, 'events.ndjson'), JSON.stringify(event) + '\n')
  return event
}
