/**
 * Tests for the shared EmptyStatePrompt (Issue 355 finding 4) — the component
 * that makes "empty state with no way out" impossible to ship. The load-bearing
 * assertion in every case is that SOME control renders: prose-only empty states
 * are the defect this component exists to prevent.
 */
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { EmptyStatePrompt } from './EmptyStatePrompt'

function renderPrompt(ui: React.ReactElement) {
  return render(<MemoryRouter>{ui}</MemoryRouter>)
}

describe('EmptyStatePrompt', () => {
  it('renders a routed action as a link carrying the destination', () => {
    renderPrompt(
      <EmptyStatePrompt
        title="Nothing published yet."
        detail="Publish a clip and this fills in."
        actionLabel="Review clips"
        actionTo="/review"
      />,
    )
    expect(screen.getByText('Nothing published yet.')).toBeInTheDocument()
    expect(screen.getByText('Publish a clip and this fills in.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Review clips' })).toHaveAttribute('href', '/review')
  })

  it('renders a handler action as a button and fires it', () => {
    const onAction = vi.fn()
    renderPrompt(<EmptyStatePrompt title="No videos yet." actionLabel="Upload" onAction={onAction} />)
    fireEvent.click(screen.getByRole('button', { name: 'Upload' }))
    expect(onAction).toHaveBeenCalledTimes(1)
  })

  it('renders a caller-supplied control verbatim', () => {
    renderPrompt(
      <EmptyStatePrompt title="No clips yet." action={<button type="button">Generate clips</button>} />,
    )
    expect(screen.getByRole('button', { name: 'Generate clips' })).toBeInTheDocument()
  })

  it('renders the secondary escape hatch only when both label and destination are given', () => {
    const { unmount } = renderPrompt(
      <EmptyStatePrompt
        title="t"
        actionLabel="Primary"
        actionTo="/a"
        secondaryLabel="Review the style instead"
        secondaryTo="/review?mode=style"
      />,
    )
    expect(screen.getByRole('link', { name: 'Review the style instead' })).toHaveAttribute(
      'href',
      '/review?mode=style',
    )
    unmount()

    renderPrompt(<EmptyStatePrompt title="t" actionLabel="Primary" actionTo="/a" secondaryLabel="Orphan" />)
    expect(screen.queryByText('Orphan')).not.toBeInTheDocument()
  })

  it('omits card chrome by default so panel call sites stay visually unchanged', () => {
    const { container } = renderPrompt(
      <EmptyStatePrompt title="t" actionLabel="Go" actionTo="/a" />,
    )
    expect(container.firstElementChild?.className).not.toMatch(/border-default/)
  })
})
