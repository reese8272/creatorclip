import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { PosterThumb } from './PosterThumb'

describe('PosterThumb', () => {
  it('renders the poster with lazy loading and reserved layout', () => {
    render(<PosterThumb src="/videos/v1/poster" alt="Desk Setup Tour" />)
    const img = screen.getByRole('img', { name: 'Desk Setup Tour' })
    expect(img).toHaveAttribute('src', '/videos/v1/poster')
    expect(img).toHaveAttribute('loading', 'lazy')
    // Intrinsic size + the aspect box keep rows from jumping as posters stream in.
    expect(img).toHaveAttribute('width', '640')
    expect(img).toHaveAttribute('height', '360')
  })

  it('shows a placeholder when there is no poster', () => {
    render(<PosterThumb src={null} alt="" />)
    expect(screen.queryByRole('img')).toBeNull()
    expect(screen.getByText('No preview')).toBeInTheDocument()
  })

  // "Not yet" and "never" are different situations a creator can act on, so the
  // placeholder distinguishes them rather than showing a uniform grey box.
  it('says WHY there is no poster', () => {
    const { rerender } = render(<PosterThumb src={null} alt="" reason="pending" />)
    expect(screen.getByText('Processing')).toBeInTheDocument()

    rerender(<PosterThumb src={null} alt="" reason="expired" />)
    expect(screen.getByText('Source expired')).toBeInTheDocument()
  })

  it('degrades to the placeholder when the image fails to load', () => {
    // A poster deleted out of band, or a race between the list response and the
    // image request, must not leave a broken-image glyph in the table.
    render(<PosterThumb src="/videos/v1/poster" alt="Clip" reason="expired" />)
    fireEvent.error(screen.getByRole('img', { name: 'Clip' }))
    expect(screen.queryByRole('img')).toBeNull()
    expect(screen.getByText('Source expired')).toBeInTheDocument()
  })

  it('takes its size from the call site, so #398/#399 can reuse it unchanged', () => {
    const { container } = render(<PosterThumb src={null} alt="" className="w-24" />)
    expect(container.firstElementChild).toHaveClass('w-24')
  })
})
