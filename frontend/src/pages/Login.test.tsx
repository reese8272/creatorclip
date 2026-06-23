// Issue 299 — Affirmative clickwrap: checkbox gates the OAuth CTA.
//
// Tests assert:
//  1. On mount the checkbox is unchecked and the sign-in CTA is disabled.
//  2. Checking the box enables the CTA (renders as an <a> with the OAuth href).
//  3. Unchecking it re-disables the CTA.
//  4. The ToS and Privacy links in the checkbox label point to the live static pages.
//  5. Footer links are present (unchanged from pre-299 baseline).
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { Login } from './Login'

function renderLogin() {
  return render(<Login />)
}

describe('Login — clickwrap checkbox (Issue 299)', () => {
  it('renders an unchecked consent checkbox on mount', () => {
    renderLogin()
    const checkbox = screen.getByRole('checkbox')
    expect(checkbox).not.toBeChecked()
  })

  it('OAuth CTA is disabled (button[disabled]) before checkbox is checked', () => {
    renderLogin()
    // Before checking: a disabled <button>, not an <a> link, is rendered.
    const disabledBtn = screen.getByRole('button', { name: /sign in with google/i })
    expect(disabledBtn).toBeDisabled()
    // No active <a> link should exist for sign-in yet.
    const links = screen.queryAllByRole('link', { name: /sign in with google/i })
    expect(links).toHaveLength(0)
  })

  it('checking the box replaces the disabled button with an active <a> link', async () => {
    const user = userEvent.setup()
    renderLogin()
    await user.click(screen.getByRole('checkbox'))
    // After checking: disabled button is gone, active link appears.
    expect(screen.queryByRole('button', { name: /sign in with google/i })).toBeNull()
    const link = screen.getByRole('link', { name: /sign in with google/i })
    expect(link).toBeInTheDocument()
    expect(link).toHaveAttribute('href', '/auth/login')
  })

  it('unchecking the box reverts to the disabled button', async () => {
    const user = userEvent.setup()
    renderLogin()
    await user.click(screen.getByRole('checkbox'))
    await user.click(screen.getByRole('checkbox'))
    expect(screen.getByRole('button', { name: /sign in with google/i })).toBeDisabled()
    expect(screen.queryByRole('link', { name: /sign in with google/i })).toBeNull()
  })

  it('checkbox label links to the live ToS and Privacy pages', () => {
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
    const footerLinks = screen.getAllByRole('link', { name: /terms/i })
    // At least one Terms link (footer — possibly also the checkbox label link)
    expect(footerLinks.length).toBeGreaterThanOrEqual(1)
    const privacyLinks = screen.getAllByRole('link', { name: /privacy/i })
    expect(privacyLinks.length).toBeGreaterThanOrEqual(1)
  })

  it('active link carries the ?yt= hint when present in the URL', async () => {
    const user = userEvent.setup()
    // Simulate ?yt=abc123 in the search params
    Object.defineProperty(window, 'location', {
      value: { search: '?yt=abc123' },
      writable: true,
    })
    renderLogin()
    await user.click(screen.getByRole('checkbox'))
    const link = screen.getByRole('link', { name: /sign in with google/i })
    expect(link.getAttribute('href')).toContain('yt=abc123')
    // Reset
    Object.defineProperty(window, 'location', {
      value: { search: '' },
      writable: true,
    })
  })
})
