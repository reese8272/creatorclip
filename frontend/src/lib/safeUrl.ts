// Shared sanitizer for server-influenced URLs bound into href/src (OWASP XSS
// Prevention Cheat Sheet: canonicalize with a real URL parser, then ALLOWLIST
// http/https only — never a javascript:/data: blocklist). Relative URLs resolve
// against the current origin and therefore pass; anything else (javascript:,
// data:, vbscript:, s3:, file:, unparseable) returns undefined so React omits
// the attribute entirely.
export function safeUrl(value: string | null | undefined): string | undefined {
  if (typeof value !== 'string') return undefined
  const trimmed = value.trim()
  if (!trimmed) return undefined
  try {
    const parsed = new URL(trimmed, window.location.origin)
    if (parsed.protocol === 'http:' || parsed.protocol === 'https:') return trimmed
  } catch {
    // Unparseable → fall through to undefined.
  }
  return undefined
}
