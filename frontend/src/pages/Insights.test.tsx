import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Insights } from './Insights'

function mockFetch() {
  const json = (body: unknown) => ({ status: 200, ok: true, json: async () => body })
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/auth/me')) return json({ channel_title: 'X' })
    if (url.endsWith('/billing/balance')) return json({ minutes_balance: 100, low_balance: false })
    if (url.endsWith('/upload-intel'))
      return json({ data_available: false, best_windows: [], optimal_gap_hours: null })
    if (url.endsWith('/insights/saved')) return json({ insights: [] })
    if (url.endsWith('/insights/analyze-performer') && init?.method === 'POST')
      return json({ id: 'ins1', content: 'Strong hook in the first 3 seconds.' })
    if (url.endsWith('/creators/me/insights'))
      return json({
        totals: { videos_analyzed: 9, shorts: 4, longs: 5, ingested_done: 9, total_minutes_processed: 120 },
        dna: { version: 2, status: 'confirmed', optimal_clip_len_s: 30, best_source_region: 'mid', optimal_upload_gap_h: 6 },
        top_performers: [
          { video_id: 'v1', youtube_video_id: 'yt1', title: 'My Top Vid', kind: 'long', performance_score: 88 },
        ],
        bottom_performers: [],
      })
    return json({})
  })
}

function renderInsights() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter basename="/app" initialEntries={['/app/insights']}>
        <Routes>
          <Route path="insights" element={<Insights />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('Insights', () => {
  it('renders the channel snapshot, top performer, and honesty disclaimer', async () => {
    vi.stubGlobal('fetch', mockFetch())
    renderInsights()
    expect(await screen.findByText('My Top Vid')).toBeInTheDocument()
    expect(screen.getByText('Channel snapshot')).toBeInTheDocument()
    expect(screen.getByText(/does not promise virality/i)).toBeInTheDocument()
  })

  it('analyzes a performer and offers to save the result', async () => {
    vi.stubGlobal('fetch', mockFetch())
    renderInsights()
    await screen.findByText('My Top Vid')
    await userEvent.click(screen.getByRole('button', { name: 'Analyze' }))
    expect(await screen.findByText(/Strong hook in the first 3 seconds/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '★ Save' })).toBeInTheDocument()
  })
})
