import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { FitBadge } from './fit-badge'

describe('FitBadge', () => {
  it('renders the channel-fit tier label, never a virality claim', () => {
    render(<FitBadge tier="strong" />)
    const badge = screen.getByText('Strong channel fit')
    expect(badge).toBeInTheDocument()
    expect(document.body.textContent ?? '').not.toMatch(/viral/i)
  })

  it('carries the honesty disclaimer (CLAUDE.md honesty constraint)', () => {
    render(<FitBadge tier="moderate" />)
    // The non-virality disclaimer must travel with every badge (title + aria).
    const badge = screen.getByText('Moderate channel fit')
    expect(badge).toHaveAttribute('title', expect.stringContaining('not a guarantee of performance'))
    expect(badge).toHaveAttribute('aria-label', expect.stringContaining('not a guarantee'))
  })

  it('renders the exploratory tier', () => {
    render(<FitBadge tier="exploratory" />)
    expect(screen.getByText('Exploratory')).toBeInTheDocument()
  })
})
