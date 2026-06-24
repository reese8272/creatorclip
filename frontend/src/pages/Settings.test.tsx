import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Settings } from './Settings'

function mockFetch() {
  const json = (body: unknown) => ({ status: 200, ok: true, json: async () => body })
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/auth/me')) return json({ channel_title: 'Test', analysis_mode: 'auto' })
    if (url.endsWith('/billing/balance')) return json({ minutes_balance: 10, low_balance: false })
    if (url.includes('/brand-kit')) return json({ caption_style: 'bold_pop', captions_enabled: true })
    if (url.includes('/api-keys')) return json({ keys: [] })
    if (url.endsWith('/creators/niches')) return json({ options: [] })
    if (url.endsWith('/creators/me/identity')) return json({ identity: null, conflict: null })
    return json({})
  })
}

function renderSettings() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter basename="/app" initialEntries={['/app/settings']}>
        <Settings />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('Settings', () => {
  it('renders the page heading + honesty disclaimer', () => {
    vi.stubGlobal('fetch', mockFetch())
    renderSettings()
    expect(screen.getByRole('heading', { name: 'Settings' })).toBeInTheDocument()
    expect(screen.getByText(/does not promise virality/i)).toBeInTheDocument()
  })

  it('marks not-yet-wired controls honestly with a "Soon" badge (Issue 308 scope)', () => {
    vi.stubGlobal('fetch', mockFetch())
    renderSettings()
    // Unbacked controls are present but clearly flagged — never faux-functional.
    expect(screen.getByText('Cut density')).toBeInTheDocument()
    expect(screen.getAllByText('Soon').length).toBeGreaterThan(0)
  })

  it('hosts the relocated editable identity form + a (disabled) footer', async () => {
    vi.stubGlobal('fetch', mockFetch())
    renderSettings()
    expect(await screen.findByRole('button', { name: 'Save identity' })).toBeInTheDocument()
    const save = screen.getByRole('button', { name: 'Save changes' })
    expect(save).toBeDisabled()
  })
})
