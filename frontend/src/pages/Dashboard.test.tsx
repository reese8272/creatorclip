import { fireEvent, render, screen, waitFor } from '@testing-library/react'
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
// `videos` is supplied per-test; clips is a map of video_id → render_status list
// used to build the batched /videos/clips/counts response (Issue 213).
// Per-video /videos/{id}/clips calls should no longer be made — if they are the
// test will still return an empty list so it doesn't mask regressions silently.
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
    // Batched counts endpoint (Issue 213 — replaces N+1 per-video queries).
    if (url.endsWith('/videos/clips/counts')) {
      const counts = Object.entries(clips).map(([video_id, cs]) => ({
        video_id,
        total: cs.length,
        rendered: cs.filter((c) => c.render_status === 'done').length,
      }))
      return json({ counts })
    }
    // Fallback: per-video clips endpoint (should not be called by the updated Dashboard).
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

  it('links to the review queue when a done video already has clips (Issue 305: Review button + Clips column)', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch([baseVideo({ id: 'vd', ingest_status: 'done' })], {
        vd: [{ render_status: 'done' }, { render_status: 'done' }],
      }),
    )
    renderDashboard()
    // The per-row action is now a "Review" link to the video's review queue
    // (the rendered count moved into the dedicated Clips column — see VideoTable test).
    const review = await screen.findByRole('link', { name: 'Review' })
    expect(review).toHaveAttribute('href', '/app/review?video_id=vd')
  })

  it('offers "Generate clips" when a done video has no clips yet', async () => {
    vi.stubGlobal('fetch', mockFetch([baseVideo({ id: 'vd', ingest_status: 'done' })], { vd: [] }))
    renderDashboard()
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Generate clips' })).toBeInTheDocument(),
    )
  })

  it('shows "Why no clips?" link for a done video with zero clips (Issue 217)', async () => {
    vi.stubGlobal('fetch', mockFetch([baseVideo({ id: 'vd', ingest_status: 'done' })], { vd: [] }))
    renderDashboard()
    // The "Why no clips?" link must appear alongside the Generate clips button.
    await waitFor(() =>
      expect(
        screen.getByRole('link', { name: /why weren't clips generated/i }),
      ).toBeInTheDocument(),
    )
  })

  it('"Why no clips?" link does not appear when the video already has clips', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch([baseVideo({ id: 'vd', ingest_status: 'done' })], {
        vd: [{ render_status: 'done' }],
      }),
    )
    renderDashboard()
    await screen.findByRole('link', { name: 'Review' })
    expect(screen.queryByRole('link', { name: /why weren't clips generated/i })).toBeNull()
  })

  it('renders the videos-first header (Issue 305)', async () => {
    vi.stubGlobal('fetch', mockFetch([baseVideo({ id: 'vd', ingest_status: 'done' })], { vd: [] }))
    renderDashboard()
    expect(await screen.findByRole('heading', { name: 'Your videos' })).toBeInTheDocument()
  })

  it('the header "+ Upload a video" button toggles the inline upload form (Issue 317)', async () => {
    vi.stubGlobal('fetch', mockFetch([baseVideo({ id: 'vd', ingest_status: 'done' })], { vd: [] }))
    renderDashboard()
    const toggle = await screen.findByRole('button', { name: '+ Upload a video' })
    expect(screen.queryByLabelText('Video file to upload')).toBeNull()
    fireEvent.click(toggle)
    expect(screen.getByLabelText('Video file to upload')).toBeInTheDocument()
  })

  it('sidebar shows the review queue with the rendered-clip count + Open review link (Issue 305)', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch([baseVideo({ id: 'vd', ingest_status: 'done' })], {
        vd: [{ render_status: 'done' }, { render_status: 'done' }],
      }),
    )
    renderDashboard()
    expect(await screen.findByText('Review queue')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open review →' })).toHaveAttribute(
      'href',
      '/app/review',
    )
  })

  it('uses the batched /videos/clips/counts endpoint (not per-video /clips calls)', async () => {
    // Issue 213: Dashboard should make exactly one clip-count call regardless of video count.
    const fetchMock = mockFetch(
      [baseVideo({ id: 'v1', ingest_status: 'done' }), baseVideo({ id: 'v2', ingest_status: 'done' })],
      { v1: [{ render_status: 'done' }], v2: [{ render_status: 'pending' }] },
    )
    vi.stubGlobal('fetch', fetchMock)
    renderDashboard()
    // Wait for clip counts to populate (both done videos get a Review action once counts load).
    await waitFor(() =>
      expect(screen.getAllByRole('link', { name: 'Review' }).length).toBeGreaterThan(0),
    )
    // Assert no per-video /clips calls were made — only the batched endpoint.
    const perVideoCalls = (fetchMock.mock.calls as [RequestInfo | URL][]).filter(([url]) =>
      String(url).match(/\/videos\/[^/]+\/clips$/),
    )
    expect(perVideoCalls).toHaveLength(0)
  })
})
