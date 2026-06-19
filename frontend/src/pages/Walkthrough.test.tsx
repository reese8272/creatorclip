import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { Walkthrough } from './Walkthrough'

// Pure client-side flow (no network). Locks the panel navigation and the
// finish side-effect (marks the walkthrough seen so it isn't shown again).
describe('Walkthrough', () => {
  beforeEach(() => localStorage.clear())

  it('advances forward on Continue and back on Back', async () => {
    const user = userEvent.setup()
    render(<Walkthrough />)
    expect(screen.getByText('1 of 5')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Continue/ }))
    expect(screen.getByText('2 of 5')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Back/ }))
    expect(screen.getByText('1 of 5')).toBeInTheDocument()
  })

  it('marks the walkthrough seen when finished on the last panel', async () => {
    const user = userEvent.setup()
    render(<Walkthrough />)
    for (let i = 0; i < 4; i++) {
      await user.click(screen.getByRole('button', { name: /Continue/ }))
    }
    expect(screen.getByText('5 of 5')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Set up my AutoClip/ }))
    expect(localStorage.getItem('creatorclip:walkthrough_seen')).toBe('1')
  })

  // OAuth-verification gate (Issue 153): this first-run page sits outside AppChrome,
  // so it must carry the ToS/Privacy footer links itself.
  it('exposes the ToS and Privacy footer links', () => {
    render(<Walkthrough />)
    expect(screen.getByRole('link', { name: 'Terms' })).toHaveAttribute('href', '/static/tos.html')
    expect(screen.getByRole('link', { name: 'Privacy' })).toHaveAttribute(
      'href',
      '/static/privacy.html',
    )
  })
})
