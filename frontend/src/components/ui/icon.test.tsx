import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { Button } from './button'
import { ArrowRight, X } from './icon'
import { ICON_SIZE } from './iconSizes'

// Issue 384 — the accessible-name contract for icons. Two rules, no exceptions:
//   icon + label  → icon is aria-hidden; the label carries the name.
//   icon only     → the BUTTON carries aria-label; the icon is still aria-hidden.
// src/test/no-glyph-icons.test.ts enforces these structurally across the app;
// these tests pin the behaviour they are enforcing.
describe('icon accessibility contract', () => {
  it('hides a decorative icon from the accessibility tree', () => {
    const { container } = render(<ArrowRight className={ICON_SIZE.md} aria-hidden="true" />)
    expect(container.querySelector('svg')).toHaveAttribute('aria-hidden', 'true')
  })

  it('names an icon+label button by its label alone', () => {
    render(
      <Button>
        Refine in editor <ArrowRight className={ICON_SIZE.md} aria-hidden="true" />
      </Button>,
    )
    // Previously this button's accessible name was "Refine in editor →", which a
    // screen reader announced as "…, right arrow".
    expect(screen.getByRole('button', { name: 'Refine in editor' })).toBeInTheDocument()
  })

  it('names an icon-only button from its own aria-label', () => {
    render(
      <button aria-label="Remove cut">
        <X className={ICON_SIZE.sm} aria-hidden="true" />
      </button>,
    )
    expect(screen.getByRole('button', { name: 'Remove cut' })).toBeInTheDocument()
  })

  it('sizes icons from the spacing scale, not lucide inline px', () => {
    const { container } = render(<X className={ICON_SIZE.sm} aria-hidden="true" />)
    const svg = container.querySelector('svg')!
    expect(svg).toHaveClass('size-3.5')
  })
})
