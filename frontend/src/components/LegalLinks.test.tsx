import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { LegalLinks } from './LegalLinks'

describe('LegalLinks', () => {
  // Google OAuth verification requires all three reachable from every surface
  // (CLAUDE.md). Assert the hrefs, not just the labels — a broken link passes a
  // text-only check.
  it('renders all three legal destinations with their static hrefs', () => {
    render(<LegalLinks />)
    expect(screen.getByRole('link', { name: 'Terms' })).toHaveAttribute('href', '/static/tos.html')
    expect(screen.getByRole('link', { name: 'Privacy' })).toHaveAttribute(
      'href',
      '/static/privacy.html',
    )
    expect(screen.getByRole('link', { name: 'Accessibility' })).toHaveAttribute(
      'href',
      '/static/accessibility.html',
    )
  })
})
