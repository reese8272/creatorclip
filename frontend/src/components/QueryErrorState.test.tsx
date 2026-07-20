/**
 * Tests for the shared QueryErrorState retry card (Issue 361 sweep) — the
 * component every core page renders on a failed page-level query so a 5xx/
 * network error never masquerades as a first-run empty state.
 */
import { render, screen, fireEvent } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { QueryErrorState } from './QueryErrorState'

describe('QueryErrorState', () => {
  it('renders the page-specific title, the default detail, and fires onRetry', () => {
    const onRetry = vi.fn()
    render(<QueryErrorState title="Couldn’t load your things." onRetry={onRetry} />)
    expect(screen.getByText('Couldn’t load your things.')).toBeInTheDocument()
    expect(screen.getByText(/usually temporary/i)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })

  it('overrides the detail line via props', () => {
    render(<QueryErrorState title="t" detail="Custom detail line." onRetry={() => {}} />)
    expect(screen.getByText('Custom detail line.')).toBeInTheDocument()
    expect(screen.queryByText(/usually temporary/i)).not.toBeInTheDocument()
  })
})
