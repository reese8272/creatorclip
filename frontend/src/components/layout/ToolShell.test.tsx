import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ToolShell } from './ToolShell'
import { HONESTY_STATEMENT } from './ToolStatusBar'

describe('ToolShell', () => {
  // CLAUDE.md hard constraint. Asserted as an EXACT string rather than a regex so
  // that softening the wording — dropping "does not promise virality", trimming
  // the estimates clause — fails loudly instead of still matching /virality/i.
  it('renders the honesty statement verbatim', () => {
    render(<ToolShell>content</ToolShell>)
    expect(screen.getByText(HONESTY_STATEMENT)).toBeInTheDocument()
    expect(HONESTY_STATEMENT).toBe(
      'AutoClip predicts fit with your style and audience — it does not promise virality. ' +
        'All scores are estimates grounded in your own channel data.',
    )
  })

  it('keeps the legal links reachable now that tool routes drop the marketing footer', () => {
    render(<ToolShell>content</ToolShell>)
    expect(screen.getByRole('link', { name: 'Terms' })).toHaveAttribute('href', '/static/tos.html')
    expect(screen.getByRole('link', { name: 'Privacy' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Accessibility' })).toBeInTheDocument()
  })

  // axe's landmark-one-main / landmark-unique gate both tool routes at serious
  // impact. The shell owns the only <main>, which is why VideoPickerLanding and
  // StyleReview were demoted to <div>.
  it('owns exactly one main landmark and one contentinfo', () => {
    render(<ToolShell>content</ToolShell>)
    expect(screen.getAllByRole('main')).toHaveLength(1)
    expect(screen.getAllByRole('contentinfo')).toHaveLength(1)
    expect(screen.getByRole('main')).toHaveTextContent('content')
  })

  it('clips main in workspace mode so children own their scroll regions', () => {
    const { rerender } = render(<ToolShell>content</ToolShell>)
    expect(screen.getByRole('main').className).toContain('lg:overflow-y-auto')
    rerender(<ToolShell scroll={false}>content</ToolShell>)
    expect(screen.getByRole('main').className).toContain('lg:overflow-hidden')
  })

  // The flex auto-minimum-size trap: without min-h-0 at every level a panel
  // refuses to shrink and overflows the clipped ancestor. There is no other
  // min-h-0 in the codebase, so no reviewer has this reflex — pin it.
  it('carries min-h-0 on the shell column and on main', () => {
    render(<ToolShell scroll={false}>content</ToolShell>)
    const shell = document.querySelector('[data-tool-shell]')
    expect(shell?.className).toContain('lg:min-h-0')
    expect(screen.getByRole('main').className).toContain('lg:min-h-0')
  })
})
