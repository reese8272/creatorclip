import { describe, expect, it } from 'vitest'
import { fitTier } from './fit'

// Boundary values are load-bearing: they ARE the product decision (DECISIONS
// 2026-06-19). Pin the exact cutoffs so a silent threshold drift is caught.
describe('fitTier', () => {
  it('maps the strong boundary (>= 0.70)', () => {
    expect(fitTier(0.7)).toBe('strong')
    expect(fitTier(1)).toBe('strong')
    expect(fitTier(0.699)).toBe('moderate') // just below the cutoff
  })

  it('maps the moderate boundary (>= 0.45)', () => {
    expect(fitTier(0.45)).toBe('moderate')
    expect(fitTier(0.5)).toBe('moderate') // the engine's fallback default
    expect(fitTier(0.449)).toBe('exploratory') // just below the cutoff
  })

  it('treats a low or missing score as exploratory (never overstates fit)', () => {
    expect(fitTier(0)).toBe('exploratory')
    expect(fitTier(null)).toBe('exploratory')
    expect(fitTier(undefined)).toBe('exploratory')
  })
})
