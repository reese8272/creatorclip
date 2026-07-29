import { describe, expect, it } from 'vitest'
import { safeUrl } from './safeUrl'

// The allowlist boundary IS the security property: http(s) + relative pass,
// every other scheme (and junk input) is dropped so React omits the attribute.
describe('safeUrl', () => {
  it('passes http(s), relative, and protocol-relative URLs', () => {
    expect(safeUrl('/app/onboarding')).toBe('/app/onboarding')
    expect(safeUrl('https://youtu.be/x')).toBe('https://youtu.be/x')
    expect(safeUrl('http://host/x')).toBe('http://host/x')
    expect(safeUrl('//host/x')).toBe('//host/x') // resolves to the page scheme
  })

  it('rejects non-http(s) schemes, including whitespace-obfuscated ones', () => {
    expect(safeUrl('javascript:alert(1)')).toBeUndefined()
    expect(safeUrl(' javascript:alert(1)')).toBeUndefined()
    expect(safeUrl('data:text/html;base64,PHNjcmlwdD4=')).toBeUndefined()
    expect(safeUrl('vbscript:x')).toBeUndefined()
    expect(safeUrl('s3://bucket/clips/c1_clean.mp4')).toBeUndefined()
    expect(safeUrl('file:///etc/passwd')).toBeUndefined()
  })

  it('rejects empty and missing values so React drops the attribute', () => {
    expect(safeUrl('')).toBeUndefined()
    expect(safeUrl('   ')).toBeUndefined()
    expect(safeUrl(null)).toBeUndefined()
    expect(safeUrl(undefined)).toBeUndefined()
  })
})
