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
    expect(await screen.findAllByText('Sign in to buy')).toHaveLength(5)
  })
})
