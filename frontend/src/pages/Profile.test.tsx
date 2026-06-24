import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Profile } from './Profile'

function mockFetch() {
  const json = (body: unknown) => ({ status: 200, ok: true, json: async () => body })
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/auth/me')) return json({ channel_title: 'My Channel', analysis_mode: 'auto' })
    if (url.endsWith('/billing/balance')) return json({ minutes_balance: 10, low_balance: false })
    if (url.endsWith('/creators/niches')) return json({ options: [] })
    if (url.endsWith('/creators/me/identity')) return json({ identity: null, conflict: null })
    if (url.endsWith('/creators/me/dna')) return json({ profile: null })
    if (url.endsWith('/videos')) return json({ videos: [] })
    if (url.includes('/insights/analytics')) return json({ metrics_available: false })
    return json({})
  })
}

function renderProfile() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter basename="/app" initialEntries={['/app/profile']}>
        <Profile />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('Profile', () => {
  it('renders the snapshot header with an Editing settings link (Issue 308)', async () => {
    vi.stubGlobal('fetch', mockFetch())
    renderProfile()
    expect(await screen.findByRole('heading', { name: 'My Channel' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Editing settings/i })).toHaveAttribute(
      'href',
      '/app/settings',
    )
  })

  it('shows the Library snapshot + honesty disclaimer', () => {
    vi.stubGlobal('fetch', mockFetch())
    renderProfile()
    expect(screen.getByText('Library')).toBeInTheDocument()
    expect(screen.getByText(/snapshot of your channel/i)).toBeInTheDocument()
  })

  it('no longer renders the relocated production controls (moved to Settings)', async () => {
    vi.stubGlobal('fetch', mockFetch())
    renderProfile()
    await screen.findByRole('heading', { name: 'My Channel' })
    // API keys / publishing / account-deletion moved to Settings.
    expect(screen.queryByText(/API keys/i)).toBeNull()
    expect(screen.queryByText(/Delete (my )?account/i)).toBeNull()
  })
})
