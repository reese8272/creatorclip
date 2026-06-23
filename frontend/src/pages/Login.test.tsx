// Issue 299 — Affirmative clickwrap: consent checkbox gates the OAuth CTA.
// Issue 300 — COPPA 13+ age attestation: a second checkbox ("I confirm I am 13
//   or older") must ALSO be checked before the CTA becomes active.
//
// Both checkboxes are required; either alone is not enough to enable sign-in.
//
// Tests assert:
//  1. On mount both checkboxes are unchecked and the CTA is disabled.
//  2. Checking only the consent box leaves the CTA disabled.
//  3. Checking only the age box leaves the CTA disabled.
//  4. Checking both boxes enables the CTA (active <a> with the OAuth href).
//  5. Unchecking either box after both are checked reverts to disabled.
//  6. The ToS and Privacy links in the consent label point to the live static pages.
//  7. Footer links are present.
//  8. The ?yt= hint is forwarded when both boxes are checked.
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { Login } from './Login'

function renderLogin() {
  return render(<Login />)
}

// Helper: check both checkboxes so the CTA is enabled.
async function checkBoth(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('checkbox', { name: /i agree to the terms/i }))
  await user.click(screen.getByRole('checkbox', { name: /i confirm i am 13/i }))
}

describe('Login — clickwrap + age gate (Issues 299 + 300)', () => {
  it('renders two unchecked checkboxes on mount', () => {
    renderLogin()
    const boxes = screen.getAllByRole('checkbox')
    expect(boxes).toHaveLength(2)
    boxes.forEach((box) => expect(box).not.toBeChecked())
  })

  it('OAuth CTA is disabled on mount (neither box checked)', () => {
    renderLogin()
    expect(screen.getByRole('button', { name: /sign in with google/i })).toBeDisabled()
    expect(screen.queryAllByRole('link', { name: /sign in with google/i })).toHaveLength(0)
  })

  it('CTA stays disabled when only the consent checkbox is checked', async () => {
    const user = userEvent.setup()
    renderLogin()
    await user.click(screen.getByRole('checkbox', { name: /i agree to the terms/i }))
    expect(screen.getByRole('button', { name: /sign in with google/i })).toBeDisabled()
    expect(screen.queryAllByRole('link', { name: /sign in with google/i })).toHaveLength(0)
  })

  it('CTA stays disabled when only the age checkbox is checked', async () => {
    const user = userEvent.setup()
    renderLogin()
    await user.click(screen.getByRole('checkbox', { name: /i confirm i am 13/i }))
    expect(screen.getByRole('button', { name: /sign in with google/i })).toBeDisabled()
    expect(screen.queryAllByRole('link', { name: /sign in with google/i })).toHaveLength(0)
  })

  it('checking both boxes enables the CTA as an active <a> link', async () => {
    const user = userEvent.setup()
    renderLogin()
    await checkBoth(user)
    expect(screen.queryByRole('button', { name: /sign in with google/i })).toBeNull()
    const link = screen.getByRole('link', { name: /sign in with google/i })
    expect(link).toBeInTheDocument()
    expect(link).toHaveAttribute('href', '/auth/login')
  })

  it('unchecking the consent box after both checked reverts to disabled', async () => {
    const user = userEvent.setup()
    renderLogin()
    await checkBoth(user)
    await user.click(screen.getByRole('checkbox', { name: /i agree to the terms/i }))
    expect(screen.getByRole('button', { name: /sign in with google/i })).toBeDisabled()
    expect(screen.queryByRole('link', { name: /sign in with google/i })).toBeNull()
  })

  it('unchecking the age box after both checked reverts to disabled', async () => {
    const user = userEvent.setup()
    renderLogin()
    await checkBoth(user)
    await user.click(screen.getByRole('checkbox', { name: /i confirm i am 13/i }))
    expect(screen.getByRole('button', { name: /sign in with google/i })).toBeDisabled()
    expect(screen.queryByRole('link', { name: /sign in with google/i })).toBeNull()
  })

  it('consent checkbox label links to the live ToS and Privacy pages', () => {
    renderLogin()
    expect(screen.getByRole('link', { name: /terms of service/i })).toHaveAttribute(
      'href',
      '/static/tos.html',
    )
    expect(screen.getByRole('link', { name: /privacy policy/i })).toHaveAttribute(
      'href',
      '/static/privacy.html',
    )
  })

  it('footer carries ToS and Privacy links', () => {
    renderLogin()
    expect(screen.getAllByRole('link', { name: /terms/i }).length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByRole('link', { name: /privacy/i }).length).toBeGreaterThanOrEqual(1)
  })

  it('active link carries the ?yt= hint when both boxes are checked', async () => {
    const user = userEvent.setup()
    Object.defineProperty(window, 'location', {
      value: { search: '?yt=abc123' },
      writable: true,
    })
    renderLogin()
    await checkBoth(user)
    const link = screen.getByRole('link', { name: /sign in with google/i })
    expect(link.getAttribute('href')).toContain('yt=abc123')
    Object.defineProperty(window, 'location', {
      value: { search: '' },
      writable: true,
    })
  })
})
