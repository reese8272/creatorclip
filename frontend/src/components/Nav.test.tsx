import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { Nav } from './Nav'
import type { Balance, CurrentUser } from '@/types'

const user: CurrentUser = { channel_title: 'Test Channel', email: 'x@y.z', analysis_mode: 'auto' }
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
    expect(screen.getByRole('link', { name: 'Dashboard' })).toHaveAttribute('href', '/app/dashboard')
    expect(screen.getByRole('link', { name: 'Review' })).toHaveAttribute('href', '/app/review')
    expect(screen.getByRole('link', { name: 'Insights' })).toHaveAttribute('href', '/app/insights')
    expect(screen.getByRole('link', { name: 'Profile' })).toHaveAttribute('href', '/app/profile')
    expect(screen.getByRole('link', { name: 'Assistant' })).toHaveAttribute('href', '/app/chat')
    expect(screen.getByRole('link', { name: 'Analyze' })).toHaveAttribute('href', '/app/analysis')
    expect(screen.getByRole('link', { name: 'Pricing' })).toHaveAttribute('href', '/app/pricing')
  })
})
