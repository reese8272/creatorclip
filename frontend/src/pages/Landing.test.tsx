// Issue 376a — public marketing landing page. Asserts the key sections
// required by the Google OAuth verification homepage requirements
// (support.google.com/cloud/answer/13464321): an honest product description,
// the verbatim honesty constraint, the real Proof-of-Lift differentiator, real
// pricing figures (billing/packs.py), and Terms/Privacy links.
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Landing } from './Landing'

describe('Landing (Issue 376a)', () => {
  it('renders the product headline and description', () => {
    render(<Landing />)
    expect(
      screen.getByRole('heading', { name: /the only ai editor that truly knows your channel/i }),
    ).toBeInTheDocument()
    expect(screen.getByText(/what autoclip does/i)).toBeInTheDocument()
  })

  it('renders the honesty constraint verbatim', () => {
    render(<Landing />)
    expect(
      screen.getByText(
        /AutoClip predicts fit with your style and audience — it does not promise virality\./,
      ),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/every recommendation is an estimate grounded in your own data, not a guarantee/i),
    ).toBeInTheDocument()
  })

  it('describes the proof-of-lift outcome loop', () => {
    render(<Landing />)
    expect(screen.getByText(/proof of lift: we measure what actually happened/i)).toBeInTheDocument()
    expect(screen.getByText(/48-hour and 7-day checkpoint/i)).toBeInTheDocument()
  })

  it('renders real pricing figures matching billing/packs.py', () => {
    render(<Landing />)
    expect(screen.getByText('$18.00')).toBeInTheDocument()
    expect(screen.getByText('$70.00')).toBeInTheDocument()
    expect(screen.getByText('$400.00')).toBeInTheDocument()
  })

  it('links Terms of Service and Privacy Policy', () => {
    render(<Landing />)
    const tosLinks = screen.getAllByRole('link', { name: /terms/i })
    const privacyLinks = screen.getAllByRole('link', { name: /privacy/i })
    expect(tosLinks.some((a) => a.getAttribute('href') === '/static/tos.html')).toBe(true)
    expect(privacyLinks.some((a) => a.getAttribute('href') === '/static/privacy.html')).toBe(true)
  })

  it('offers a sign-in CTA rather than a signup-required claim', () => {
    render(<Landing />)
    const signInLinks = screen.getAllByRole('link', { name: /sign in/i })
    expect(signInLinks.length).toBeGreaterThan(0)
    expect(screen.queryByText(/no signup required/i)).not.toBeInTheDocument()
  })
})
