import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it } from 'vitest'
import { Walkthrough } from './Walkthrough'

// Pure client-side flow (no network). useNavigate needs a Router; a stub
// /onboarding route lets us assert the finish hand-off is an in-SPA navigation
// (Issue 154) rather than the old full-page exit to /static/onboarding.html.
function renderWalkthrough() {
  return render(
    <MemoryRouter basename="/app" initialEntries={['/app/walkthrough']}>
      <Routes>
        <Route path="walkthrough" element={<Walkthrough />} />
        <Route path="onboarding" element={<div>Onboarding route</div>} />
        <Route path="dashboard" element={<div>Dashboard route</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('Walkthrough', () => {
  beforeEach(() => localStorage.clear())

  it('advances forward on Continue and back on Back', async () => {
    const user = userEvent.setup()
    renderWalkthrough()
    expect(screen.getByText('1 of 5')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Continue/ }))
    expect(screen.getByText('2 of 5')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Back/ }))
    expect(screen.getByText('1 of 5')).toBeInTheDocument()
  })

  it('marks seen and hands off to the in-SPA onboarding route when finished', async () => {
    const user = userEvent.setup()
    renderWalkthrough()
    for (let i = 0; i < 4; i++) {
      await user.click(screen.getByRole('button', { name: /Continue/ }))
    }
    expect(screen.getByText('5 of 5')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Set up my AutoClip/ }))
    expect(localStorage.getItem('creatorclip:walkthrough_seen')).toBe('1')
    // Issue 154: stays inside the SPA (no full-page exit to /static/onboarding.html).
    expect(screen.getByText('Onboarding route')).toBeInTheDocument()
  })

  it('offers a skip-to-dashboard escape hatch that marks the walkthrough seen', async () => {
    const user = userEvent.setup()
    renderWalkthrough()
    await user.click(screen.getByRole('button', { name: /Skip to dashboard/ }))
    expect(localStorage.getItem('creatorclip:walkthrough_seen')).toBe('1')
    expect(screen.getByText('Dashboard route')).toBeInTheDocument()
  })

  // OAuth-verification gate (Issue 153): this first-run page sits outside AppChrome,
  // so it must carry the ToS/Privacy footer links itself.
  it('exposes the ToS and Privacy footer links', () => {
    renderWalkthrough()
    expect(screen.getByRole('link', { name: 'Terms' })).toHaveAttribute('href', '/static/tos.html')
    expect(screen.getByRole('link', { name: 'Privacy' })).toHaveAttribute(
      'href',
      '/static/privacy.html',
    )
  })
})
