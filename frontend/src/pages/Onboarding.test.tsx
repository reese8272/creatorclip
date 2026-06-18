import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Onboarding } from './Onboarding'

// Route the page's several reads by URL. `identity` is the load-bearing knob:
// the Build-DNA step stays locked until an identity row exists (Issue 100).
function mockFetch(opts: { identity?: unknown; ready?: boolean } = {}) {
  const json = (body: unknown) => ({ status: 200, ok: true, json: async () => body })
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/auth/me')) return json({ channel_title: 'Reese TV' })
    if (url.endsWith('/billing/balance')) return json({ minutes_balance: 50, low_balance: false })
    if (url.endsWith('/data-gate'))
      return json({
        long_form_videos: opts.ready ? 12 : 1,
        shorts: opts.ready ? 6 : 0,
        long_form_ready: !!opts.ready,
        shorts_ready: !!opts.ready,
        ready: !!opts.ready,
      })
    if (url.endsWith('/creators/me/dna')) return json({ profile: null })
    if (url.endsWith('/creators/me/identity')) return json({ identity: opts.identity ?? null })
    if (url.endsWith('/creators/niches')) return json({ options: [{ id: 'gaming', label: 'Gaming' }] })
    return json({})
  })
}

function renderOnboarding() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter basename="/app" initialEntries={['/app/onboarding']}>
        <Routes>
          <Route path="onboarding" element={<Onboarding />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('Onboarding', () => {
  it('shows the connected channel, the honesty disclaimer, and data-gate readiness', async () => {
    vi.stubGlobal('fetch', mockFetch({ ready: true }))
    renderOnboarding()
    expect(await screen.findByText(/Connected as Reese TV/)).toBeInTheDocument()
    expect(screen.getByText(/does not promise virality/i)).toBeInTheDocument()
    expect(await screen.findByText('Ready to build your Creator DNA.')).toBeInTheDocument()
  })

  it('locks the Build-DNA step until an identity row exists', async () => {
    vi.stubGlobal('fetch', mockFetch({ identity: null }))
    renderOnboarding()
    const build = await screen.findByRole('button', { name: 'Build Creator DNA' })
    expect(build).toBeDisabled()
    expect(screen.getByText(/Finish step 3 first/)).toBeInTheDocument()
  })

  it('unlocks Build-DNA when the creator already has an identity on file', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch({ identity: { version: 1, niches: ['gaming'], audience_summary: 'gamers' } }),
    )
    renderOnboarding()
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Build Creator DNA' })).toBeEnabled(),
    )
  })
})
