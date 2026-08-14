import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { Nav } from './Nav'
import type { Balance, CurrentUser } from '@/types'

const user: CurrentUser = { channel_title: 'Test Channel', email: 'x@y.z' }
const balance: Balance = { minutes_balance: 42, low_balance: false }

function renderNav() {
  return render(
    <MemoryRouter basename="/app" initialEntries={['/app/profile']}>
      <Nav user={user} balance={balance} />
    </MemoryRouter>,
  )
}

describe('Nav', () => {
  it('renders the creator title and balance chip', () => {
    renderNav()
    expect(screen.getByText('Test Channel')).toBeInTheDocument()
    expect(screen.getByText('42 min')).toBeInTheDocument()
  })

  it('routes every nav destination client-side under the /app basename (all pages ported, 85f)', () => {
    renderNav()
    // Issue 355: labels are the pages' own names now — "Videos" is the h1 of
    // /dashboard, "Channel" is what /profile is (its h1 is the channel's name).
    expect(screen.getByRole('link', { name: 'Videos' })).toHaveAttribute('href', '/app/dashboard')
    expect(screen.getByRole('link', { name: 'Review' })).toHaveAttribute('href', '/app/review')
    expect(screen.getByRole('link', { name: 'Editor' })).toHaveAttribute('href', '/app/editor')
    expect(screen.getByRole('link', { name: 'Insights' })).toHaveAttribute('href', '/app/insights')
    expect(screen.getByRole('link', { name: 'Assistant' })).toHaveAttribute('href', '/app/chat')
    expect(screen.getByRole('link', { name: 'Channel' })).toHaveAttribute('href', '/app/profile')
    expect(screen.getByRole('link', { name: 'Settings' })).toHaveAttribute('href', '/app/settings')
    expect(screen.getByRole('link', { name: 'Pricing' })).toHaveAttribute('href', '/app/pricing')
  })

  it('gives every destination a one-line "what is this" (Issue 355 finding 1)', () => {
    renderNav()
    // Desktop carries the blurb as a tooltip; the mobile panel renders it
    // visibly. Either way no nav word is left unexplained.
    expect(screen.getByRole('link', { name: 'Videos' })).toHaveAttribute(
      'title',
      "Everything you've uploaded, and what's been clipped",
    )
    fireEvent.click(screen.getByRole('button', { name: 'Open menu' }))
    expect(screen.getByText('Ask anything about your channel, in your own words')).toBeInTheDocument()
  })

  it('points the wordmark and the balance pill at in-app destinations', () => {
    renderNav()
    // Signed in, the wordmark used to leave the SPA for the marketing landing,
    // which the server bounced straight back to the dashboard.
    expect(screen.getByRole('link', { name: 'AutoClip' })).toHaveAttribute(
      'href',
      '/app/dashboard',
    )
    expect(screen.getByRole('link', { name: '42 min' })).toHaveAttribute('href', '/app/pricing')
  })

  it('collapses the links behind a toggle on mobile (Issue 163)', () => {
    renderNav()
    const toggle = screen.getByRole('button', { name: 'Open menu' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(toggle)
    expect(screen.getByRole('button', { name: 'Close menu' })).toHaveAttribute(
      'aria-expanded',
      'true',
    )
  })
})
