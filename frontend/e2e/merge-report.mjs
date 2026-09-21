// Fold e2e/.audit/events.ndjson + Playwright JSON reporter outputs into
// e2e/.audit/report.json (Issue 542). Run by scripts/ui_audit.sh after the
// lanes finish; cwd must be frontend/.
//
//   node e2e/merge-report.mjs mocked=0 local=1
//
// Each arg is <rung>=<exit-code> for a rung that ran. Playwright JSON output
// is read from e2e/.audit/pw-<rung>.json when present. Exit code: 0 iff every
// declared rung exited 0.

import * as fs from 'node:fs'
import * as path from 'node:path'

const AUDIT = path.resolve('e2e/.audit')

const rungArgs = process.argv.slice(2).map((a) => {
  const [rung, code] = a.split('=')
  return { rung, exitCode: Number(code) }
})

function pwSummary(rung) {
  const p = path.join(AUDIT, `pw-${rung}.json`)
  if (!fs.existsSync(p)) return null
  try {
    const stats = JSON.parse(fs.readFileSync(p, 'utf8')).stats ?? {}
    return {
      expected: stats.expected ?? 0,
      unexpected: stats.unexpected ?? 0,
      flaky: stats.flaky ?? 0,
      skipped: stats.skipped ?? 0,
      durationMs: Math.round(stats.duration ?? 0),
    }
  } catch {
    return null
  }
}

const events = []
const ndjson = path.join(AUDIT, 'events.ndjson')
if (fs.existsSync(ndjson)) {
  for (const line of fs.readFileSync(ndjson, 'utf8').split('\n')) {
    if (!line.trim()) continue
    try {
      events.push(JSON.parse(line))
    } catch {
      /* torn write — skip the line, never the report */
    }
  }
}

const rungs = {}
for (const { rung, exitCode } of rungArgs) {
  const evs = events.filter((e) => e.rung === rung)
  rungs[rung] = {
    ok: exitCode === 0,
    exitCode,
    playwright: pwSummary(rung),
    routesAudited: evs.length,
    routesWithConsoleErrors: evs.filter((e) => e.consoleErrors?.length).length,
    routesWithPageErrors: evs.filter((e) => e.pageErrors?.length).length,
    routesWithFailedRequests: evs.filter((e) => e.failedRequests?.length).length,
    routesWithAxeViolations: evs.filter((e) => e.axeViolations?.length).length,
  }
}

const report = {
  ok: rungArgs.every((r) => r.exitCode === 0),
  generatedAt: new Date().toISOString(),
  rungs,
  counts: {
    events: events.length,
    consoleErrors: events.reduce((n, e) => n + (e.consoleErrors?.length ?? 0), 0),
    pageErrors: events.reduce((n, e) => n + (e.pageErrors?.length ?? 0), 0),
    failedRequests: events.reduce((n, e) => n + (e.failedRequests?.length ?? 0), 0),
    axeViolations: events.reduce((n, e) => n + (e.axeViolations?.length ?? 0), 0),
  },
  events,
}

fs.mkdirSync(AUDIT, { recursive: true })
fs.writeFileSync(path.join(AUDIT, 'report.json'), JSON.stringify(report, null, 2))
console.log(
  `report.json: ok=${report.ok} rungs=${Object.keys(rungs).join(',')} ` +
    `events=${report.counts.events} consoleErrors=${report.counts.consoleErrors} ` +
    `pageErrors=${report.counts.pageErrors} failedRequests=${report.counts.failedRequests}`,
)
process.exit(report.ok ? 0 : 1)
