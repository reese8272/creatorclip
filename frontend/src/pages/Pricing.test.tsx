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
