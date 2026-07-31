/**
 * Tests for AskSurfaceTabs (Issue 355 finding 2) — the row that tells a first-run
 * creator apart the three surfaces that all answer "why did this happen / what
 * next". The load-bearing property is that each surface is reachable from the
 * other two AND carries a one-liner, since "Analyze" no longer has a nav entry.
 */
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { AskSurfaceTabs } from './AskSurfaceTabs'

describe('AskSurfaceTabs', () => {
  it('links all three surfaces, so /analysis stays reachable without a nav entry', () => {
    render(
      <MemoryRouter>
        <AskSurfaceTabs current="assistant" />
      </MemoryRouter>,
    )
    expect(screen.getByRole('link', { name: /Assistant/ })).toHaveAttribute('href', '/chat')
    expect(screen.getByRole('link', { name: /Analyze one video/ })).toHaveAttribute(
      'href',
      '/analysis',
    )
    expect(screen.getByRole('link', { name: /Insights/ })).toHaveAttribute('href', '/insights')
  })

  it('explains each surface in one line, which is what the nav could not do', () => {
    render(
      <MemoryRouter>
        <AskSurfaceTabs current="analyze" />
      </MemoryRouter>,
    )
    expect(screen.getByText('Ask about your whole channel')).toBeInTheDocument()
    expect(screen.getByText('Why did this video perform that way?')).toBeInTheDocument()
    expect(screen.getByText('Auto-generated report, no question needed')).toBeInTheDocument()
  })

  it('marks only the current surface with aria-current', () => {
    render(
      <MemoryRouter>
        <AskSurfaceTabs current="insights" />
      </MemoryRouter>,
    )
    expect(screen.getByRole('link', { name: /Insights/ })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('link', { name: /Assistant/ })).not.toHaveAttribute('aria-current')
  })
})
