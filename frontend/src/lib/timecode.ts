// H:MM:SS (or M:SS) for source-relative timecodes, which can run to hours.
// Shared by the long-form editor surfaces (master timeline, transcript panel).
export function fmtClock(s: number): string {
  const sec = Math.max(0, Math.floor(s))
  const h = Math.floor(sec / 3600)
  const m = Math.floor((sec % 3600) / 60)
  const ss = (sec % 60).toString().padStart(2, '0')
  return h > 0 ? `${h}:${m.toString().padStart(2, '0')}:${ss}` : `${m}:${ss}`
}

// Parse a "H:MM:SS" / "M:SS" / "S" timestamp into seconds; NaN-safe → 0.
export function parseClock(ts: string): number {
  const parts = ts.split(':').map((p) => parseInt(p, 10))
  if (parts.some(Number.isNaN)) return 0
  return parts.reduce((acc, n) => acc * 60 + n, 0)
}
