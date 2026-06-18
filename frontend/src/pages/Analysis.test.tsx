import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Analysis } from './Analysis'

function mockFetch() {
  const json = (body: unknown) => ({ status: 200, ok: true, json: async () => body })
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/auth/me')) return json({ channel_title: 'X' })
    if (url.endsWith('/billing/balance')) return json({ minutes_balance: 100, low_balance: false })
    return json({})
  })
}

function renderAnalysis(entry: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter basename="/app" initialEntries={[entry]}>
        <Routes>
          <Route path="analysis" element={<Analysis />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('Analysis', () => {
  it('shows the query form + honesty disclaimer, and hides per-video panels without a video_id', () => {
    vi.stubGlobal('fetch', mockFetch())
    renderAnalysis('/app/analysis')
    expect(screen.getByRole('heading', { name: 'Analyze a video' })).toBeInTheDocument()
    expect(screen.getByText(/does not promise virality/i)).toBeInTheDocument()
    expect(screen.queryByText('Title Optimizer')).not.toBeInTheDocument()
    expect(screen.queryByText('Hook Analyzer')).not.toBeInTheDocument()
  })

  it('reveals the four per-video features when arrived at with ?video_id=', () => {
    vi.stubGlobal('fetch', mockFetch())
    renderAnalysis('/app/analysis?video_id=abc123&video_title=My%20Vid')
    expect(screen.getByText('Title Optimizer')).toBeInTheDocument()
    expect(screen.getByText('Hook Analyzer')).toBeInTheDocument()
    expect(screen.getByText('Chapter Markers')).toBeInTheDocument()
    expect(screen.getByText('Thumbnail Concepts')).toBeInTheDocument()
  })
})
