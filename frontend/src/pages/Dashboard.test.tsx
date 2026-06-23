import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Dashboard } from './Dashboard'
import type { Video } from '@/types'

// Minimal EventSource stub so VideoRow's useStageStream hook doesn't throw in
// jsdom. The hook opens a connection for in-flight videos; Dashboard tests don't
// need to assert on SSE events — they just need the render not to crash.
class NoopEventSource {
  closed = false
  addEventListener() {}
  close() { this.closed = true }
}

// Route the SPA fetch by URL so one mock serves the dashboard's several calls.
// `videos` is supplied per-test; everything else returns a benign default.
function mockFetch(videos: Video[], clips: Record<string, { render_status: string }[]> = {}) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    const json = (body: unknown) => ({ status: 200, ok: true, json: async () => body })
    if (url.endsWith('/auth/me'))
      return json({ channel_title: 'Test', analysis_mode: 'manual', setup: { step: 'complete' } })
    if (url.endsWith('/billing/balance'))
      return json({ minutes_balance: 100, low_balance: false })
    if (url.endsWith('/creators/me/dna')) return json({ profile: null })
    if (url.includes('/insights/analytics')) return json({ metrics_available: false })
    if (url.endsWith('/videos'))
      return json({ videos, state: videos.length ? 'populated' : 'empty_initial' })
    const clipMatch = url.match(/\/videos\/([^/]+)\/clips$/)
    if (clipMatch) return json({ clips: clips[clipMatch[1]] ?? [] })
    return json({})
  })
}

function renderDashboard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter basename="/app" initialEntries={['/app/dashboard']}>
        <Routes>
          <Route path="dashboard" element={<Dashboard />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

const baseVideo = (over: Partial<Video>): Video => ({
  id: 'v1',
  youtube_video_id: 'abcdef12345',
  title: 'My video',
  kind: 'long',
  ingest_status: 'pending',
  duration_s: 600,
  created_at: '2026-06-01T00:00:00Z',
  origin: 'upload',
  clippable: true,
  ...over,
})

beforeEach(() => vi.stubGlobal('EventSource', NoopEventSource))
afterEach(() => vi.unstubAllGlobals())

describe('Dashboard', () => {
  it('shows the empty-state hero and honesty disclaimer when there are no videos', async () => {
    vi.stubGlobal('fetch', mockFetch([]))
    renderDashboard()
    expect(await screen.findByText("Let's get your first clip.")).toBeInTheDocument()
    // Honesty constraint (CLAUDE.md) must survive the redesign.
    expect(
      screen.getAllByText(/does not promise virality/i).length,
    ).toBeGreaterThan(0)
  })

  it('offers "Queue for analysis" on a pending clippable video', async () => {
    vi.stubGlobal('fetch', mockFetch([baseVideo({ ingest_status: 'pending', clippable: true })]))
    renderDashboard()
    expect(await screen.findByRole('button', { name: 'Queue for analysis' })).toBeInTheDocument()
  })

  it('offers the upload affordance (not a queue CTA) for a non-clippable linked video', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch([baseVideo({ ingest_status: 'pending', clippable: false, origin: 'link' })]),
    )
    renderDashboard()
    expect(await screen.findByText('Upload source file to clip')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Queue for analysis' })).not.toBeInTheDocument()
  })

  it('links to the review queue when a done video already has clips', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch([baseVideo({ id: 'vd', ingest_status: 'done' })], {
        vd: [{ render_status: 'done' }, { render_status: 'done' }],
      }),
    )
    renderDashboard()
    expect(await screen.findByRole('link', { name: '2 clips' })).toBeInTheDocument()
  })

  it('offers "Generate clips" when a done video has no clips yet', async () => {
    vi.stubGlobal('fetch', mockFetch([baseVideo({ id: 'vd', ingest_status: 'done' })], { vd: [] }))
    renderDashboard()
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Generate clips' })).toBeInTheDocument(),
    )
  })
})
