import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Pricing } from './Pricing'

// The load-bearing pricing behaviour: it must render the price grid for
// ANONYMOUS visitors (no redirect to login) — the reason pricing lives outside
// the auth gate — and offer a sign-in CTA rather than a buy button.
function renderPricing() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter basename="/app" initialEntries={['/app/pricing']}>
        <Routes>
          <Route path="pricing" element={<Pricing />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('Pricing', () => {
  it('shows the price grid and a sign-in CTA for anonymous visitors', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ status: 401, ok: false, json: async () => ({}) }),
    )
    renderPricing()
    expect(screen.getByText('Buy the minutes you need.')).toBeInTheDocument()
    // Issue 209: Stream pack added → 6 purchasable packs (was 5)
    expect(await screen.findAllByText('Sign in to buy')).toHaveLength(6)
  })

  it('renders the Stream pack (Issue 209)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ status: 401, ok: false, json: async () => ({}) }),
    )
    renderPricing()
    expect(await screen.findByText('Stream')).toBeInTheDocument()
  })

  it('renders the refund policy copy (Issue 208)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ status: 401, ok: false, json: async () => ({}) }),
    )
    renderPricing()
    expect(await screen.findByText(/Refund policy/i)).toBeInTheDocument()
  })
})

// ── Issue 454: buyPack behaviour — the client half of purchase-attempt-scoped
// idempotency. These are the first tests to exercise buyPack at all (the gap
// that let Issues 453 and 454 ship).

import { fireEvent, waitFor } from '@testing-library/react'

type FetchInit = { method?: string; body?: string }

function authedFetch(checkout: (init: FetchInit) => Promise<unknown> | unknown) {
  return vi.fn().mockImplementation(async (url: unknown, init?: FetchInit) => {
    const u = String(url)
    if (u.includes('/billing/checkout')) return checkout(init ?? {})
    if (u.includes('/auth/me'))
      return { status: 200, ok: true, json: async () => ({ id: 'c1', email: 'c@example.com' }) }
    if (u.includes('/billing/balance'))
      return {
        status: 200,
        ok: true,
        json: async () => ({ minutes_balance: 100, trial_active: false, low_balance: false }),
      }
    return { status: 404, ok: false, json: async () => ({}) }
  })
}

function stubLocation() {
  const assign = vi.fn()
  vi.stubGlobal('location', { ...window.location, origin: 'http://localhost', assign })
  return assign
}

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

describe('Pricing buyPack (Issue 454)', () => {
  it('POSTs one checkout with a v4 intent_id and follows the returned URL', async () => {
    const assign = stubLocation()
    const bodies: string[] = []
    const fetchMock = authedFetch((init) => {
      bodies.push(init.body ?? '')
      return {
        status: 200,
        ok: true,
        json: async () => ({ checkout_url: 'https://checkout.stripe.test/s1' }),
      }
    })
    vi.stubGlobal('fetch', fetchMock)
    renderPricing()
    fireEvent.click((await screen.findAllByText('Buy now'))[0])
    await waitFor(() => expect(assign).toHaveBeenCalledWith('https://checkout.stripe.test/s1'))
    expect(bodies).toHaveLength(1)
    const body = JSON.parse(bodies[0])
    expect(body.pack_id).toBe('starter')
    expect(body.intent_id).toMatch(UUID_V4)
  })

  it('a double-click issues exactly ONE POST and disables the buttons while in flight', async () => {
    stubLocation()
    let resolveCheckout!: (v: unknown) => void
    const pending = new Promise((r) => (resolveCheckout = r))
    const fetchMock = authedFetch(() => pending)
    vi.stubGlobal('fetch', fetchMock)
    renderPricing()
    const buy = (await screen.findAllByText('Buy now'))[0]
    fireEvent.click(buy)
    fireEvent.click(buy)
    await waitFor(() => {
      const posts = fetchMock.mock.calls.filter(([u]) => String(u).includes('/billing/checkout'))
      expect(posts).toHaveLength(1)
    })
    // The clicked button reports busy; every buy button is disabled for the attempt.
    const busy = screen.getByText('Starting checkout…').closest('button')!
    expect(busy).toHaveAttribute('aria-busy', 'true')
    for (const label of screen.getAllByText('Buy now')) {
      expect(label.closest('button')).toBeDisabled()
    }
    resolveCheckout({
      status: 200,
      ok: true,
      json: async () => ({ checkout_url: 'https://checkout.stripe.test/s1' }),
    })
    await waitFor(() =>
      expect(screen.queryByText('Starting checkout…')).not.toBeInTheDocument(),
    )
  })

  it('each settled attempt gets a FRESH intent_id — same pack and cross-pack', async () => {
    stubLocation()
    const bodies: string[] = []
    const fetchMock = authedFetch((init) => {
      bodies.push(init.body ?? '')
      return {
        status: 200,
        ok: true,
        json: async () => ({ checkout_url: 'https://checkout.stripe.test/s' }),
      }
    })
    vi.stubGlobal('fetch', fetchMock)
    renderPricing()
    const buttons = await screen.findAllByText('Buy now')
    fireEvent.click(buttons[0])
    await waitFor(() => expect(bodies).toHaveLength(1))
    fireEvent.click(buttons[0]) // repeat purchase, same pack
    await waitFor(() => expect(bodies).toHaveLength(2))
    fireEvent.click(buttons[1]) // different pack in the same tab
    await waitFor(() => expect(bodies).toHaveLength(3))
    const [a, b, c] = bodies.map((raw) => JSON.parse(raw))
    expect(a.intent_id).toMatch(UUID_V4)
    expect(b.intent_id).toMatch(UUID_V4)
    expect(c.intent_id).toMatch(UUID_V4)
    expect(new Set([a.intent_id, b.intent_id, c.intent_id]).size).toBe(3)
    expect(c.pack_id).not.toBe(a.pack_id)
  })

  it('surfaces 409 checkout_conflict distinctly and says nothing was charged', async () => {
    stubLocation()
    const fetchMock = authedFetch(() => ({
      status: 409,
      ok: false,
      json: async () => ({
        detail: { code: 'checkout_conflict', message: 'conflict' },
      }),
    }))
    vi.stubGlobal('fetch', fetchMock)
    renderPricing()
    fireEvent.click((await screen.findAllByText('Buy now'))[0])
    expect(await screen.findByText(/conflicted with an earlier one/)).toBeInTheDocument()
    expect(screen.getByText(/Nothing was charged/)).toBeInTheDocument()
  })

  it('generic failure keeps the “nothing was charged” promise and re-enables buying', async () => {
    stubLocation()
    const fetchMock = authedFetch(() => ({
      status: 502,
      ok: false,
      json: async () => ({ detail: { code: 'checkout_failed', message: 'nope' } }),
    }))
    vi.stubGlobal('fetch', fetchMock)
    renderPricing()
    const buy = (await screen.findAllByText('Buy now'))[0]
    fireEvent.click(buy)
    expect(await screen.findByText(/nothing was charged/i)).toBeInTheDocument()
    await waitFor(() => expect(buy.closest('button')).not.toBeDisabled())
  })
})
